"""Encrypted, future-only rule batches with durable, fail-closed recovery.

Planning only reads rules and POSTs read-only previews. Execution is opt-in and
never retries a dispatched mutation. The journal is an encrypted append-only
checkpoint chain; a crash during a checkpoint fails closed for manual recovery.
"""

from __future__ import annotations

import copy
import os
import re
from contextlib import contextmanager
from pathlib import Path

from simplifi.errors import ReadOnlyViolation, SimplifiError
from simplifi.transaction_rules import (
    EDITABLE,
    _request,
    digest,
    load_encrypted,
    preview_rule,
    rule_payload,
    save_encrypted,
)

COMPARE = EDITABLE | {
    "applyToExistingTransactions",
    "prevTransactionRuleId",
    "nextTransactionRuleId",
}


def _rules(client):
    rows = client.transaction_rules()
    result = {r["id"]: r for r in rows}
    if len(result) != len(rows):
        raise SimplifiError("Duplicate rule IDs in server response")
    return result


def _validate(plan):
    if (
        not isinstance(plan, dict)
        or set(plan) != {"version", "kind", "dataset_id", "baseline", "operations"}
        or plan["version"] != 1
        or plan["kind"] != "rule-change-plan"
        or not plan["dataset_id"]
        or not isinstance(plan["baseline"], dict)
        or not isinstance(plan["operations"], list)
        or not plan["operations"]
    ):
        raise ReadOnlyViolation("Invalid rule change plan")
    seen = set()
    for op in plan["operations"]:
        if not isinstance(op, dict) or set(op) != {
            "action",
            "rule_id",
            "original",
            "payload",
            "preview",
        }:
            raise ReadOnlyViolation("Invalid planned operation")
        rid = op["rule_id"]
        if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", rid) or rid in seen:
            raise ReadOnlyViolation("Use each valid rule ID at most once per plan")
        seen.add(rid)
        if (
            not isinstance(op["original"], dict)
            or plan["baseline"].get(rid) != op["original"]
            or op["original"].get("id") != rid
        ):
            raise ReadOnlyViolation("Rule original differs from the plan baseline")
        if op["original"].get("isDeleted"):
            raise ReadOnlyViolation("An active rule is required")
        if op["action"] == "update":
            payload = op["payload"]
            if (
                not isinstance(payload, dict)
                or payload.get("applyToExistingTransactions") is not False
            ):
                raise ReadOnlyViolation("Batch plans only support future transactions")
            changes = {
                k: payload.get(k) for k in EDITABLE if payload.get(k) != op["original"].get(k)
            }
            if rule_payload(op["original"], changes) != payload:
                raise ReadOnlyViolation("Unsupported payload in rule plan")
            if not isinstance(op["preview"], dict) or not isinstance(
                op["preview"].get("transactions"), list
            ):
                raise ReadOnlyViolation("Missing update preview")
        elif op["action"] != "delete" or op["payload"] is not None or op["preview"] is not None:
            raise ReadOnlyViolation("Unsupported planned operation")


def inspect_change_plan(plan_path):
    """Return a reviewable diff and confirmation digest without authenticating."""
    plan = load_encrypted(Path(plan_path))
    _validate(plan)
    operations = []
    for op in plan["operations"]:
        row = {"action": op["action"], "rule_id": op["rule_id"]}
        if op["action"] == "delete":
            row["original"] = op["original"]
        else:
            row["changes"] = {
                k: {"before": op["original"].get(k), "after": op["payload"].get(k)}
                for k in sorted(EDITABLE)
                if op["original"].get(k) != op["payload"].get(k)
            }
            p = op["preview"]
            row["preview"] = {
                k: p.get(k) for k in ("srcTxnsCount", "resultTxnsCount", "previewTxnsEndDate")
            }
            row["preview"]["sample_count"] = len(p["transactions"])
            row["preview"]["exhaustive"] = False
        operations.append(row)
    return {"confirmation": digest(plan), "apply_to_existing": False, "operations": operations}


def prepare_change_plan(client, operations, *, plan_path):
    """operations: [{action: update|delete, rule_id: ..., changes: {...}}]."""
    if not client.dataset_id:
        raise SimplifiError("Resolve a dataset before planning rule changes")
    if not isinstance(operations, list) or not operations:
        raise ReadOnlyViolation("A nonempty operation list is required")
    baseline = _rules(client)
    planned = []
    for item in operations:
        if not isinstance(item, dict) or set(item) - {"action", "rule_id", "changes"}:
            raise ReadOnlyViolation("Unsupported operation fields")
        action, rid = item.get("action"), item.get("rule_id")
        if not isinstance(rid, str) or rid not in baseline:
            raise ReadOnlyViolation("Select an existing rule ID")
        original = copy.deepcopy(baseline[rid])
        payload = preview = None
        if action == "update":
            changes = item.get("changes")
            if not isinstance(changes, dict) or not changes:
                raise ReadOnlyViolation("An update requires a changes object")
            payload = rule_payload(original, changes)
        elif action != "delete" or "changes" in item:
            raise ReadOnlyViolation("Deletion accepts only action and rule_id")
        planned.append(
            {
                "action": action,
                "rule_id": rid,
                "original": original,
                "payload": payload,
                "preview": preview,
            }
        )
    # Validate the entire input before making even read-only preview requests.
    for op in planned:
        if op["action"] == "update":
            op["preview"] = {"transactions": []}
    plan = {
        "version": 1,
        "kind": "rule-change-plan",
        "dataset_id": client.dataset_id,
        "baseline": baseline,
        "operations": planned,
    }
    _validate(plan)
    for op in planned:
        if op["action"] == "update":
            changes = {
                k: op["payload"].get(k)
                for k in EDITABLE
                if op["payload"].get(k) != op["original"].get(k)
            }
            op["preview"] = preview_rule(client, op["original"], changes)
    if _rules(client) != baseline:
        raise SimplifiError("Rules changed during planning; prepare a new plan")
    save_encrypted(Path(plan_path), plan)
    return inspect_change_plan(plan_path)


@contextmanager
def _lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "executor.lock").open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SimplifiError("Another executor holds this journal lock") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _read_journal(directory):
    files = sorted(directory.glob("checkpoint-*.dpapi"))
    last = None
    for index, path in enumerate(files):
        if path.name != f"checkpoint-{index:08d}.dpapi":
            raise SimplifiError("Journal checkpoint gap; manual recovery required")
        state = load_encrypted(path)
        if (
            state.get("sequence") != index
            or state.get("version") != 1
            or state.get("previous") != (digest(last) if last else None)
        ):
            raise SimplifiError("Journal checkpoint chain invalid; manual recovery required")
        last = state
    return last


def _checkpoint(directory, state):
    # Callers mutate their in-memory state; chain to the last durable checkpoint.
    previous = load_encrypted(directory / f"checkpoint-{state['sequence']:08d}.dpapi")
    next_state = copy.deepcopy(state)
    next_state.update(sequence=state["sequence"] + 1, previous=digest(previous))
    save_encrypted(directory / f"checkpoint-{next_state['sequence']:08d}.dpapi", next_state)
    return next_state


def change_plan_status(journal_dir):
    """Local-only status, including any dispatched operation needing reconciliation."""
    state = _read_journal(Path(journal_dir))
    if state is None:
        return {"started": False}
    return {
        "started": True,
        "confirmation": state["plan_digest"],
        "states": [s["status"] for s in state["steps"]],
        "complete": all(s["status"] == "verified" for s in state["steps"]),
    }


def _matches(op, current):
    if op["action"] == "delete":
        return current is None
    return current is not None and all(current.get(k) == op["payload"].get(k) for k in COMPARE)


def execute_change_plan(client, plan_path, *, confirmation, journal_dir):
    """Execute or resume. Dispatched operations are read/reconciled, never replayed.

    A still-original or conflicting record after dispatch is ambiguous and blocks
    further writes. A matching result is checkpointed as verified and skipped.
    """
    plan = load_encrypted(Path(plan_path))
    _validate(plan)
    if digest(plan) != confirmation or plan["dataset_id"] != client.dataset_id:
        raise ReadOnlyViolation("Confirmation or dataset differs from the rule plan")
    directory = Path(journal_dir)
    with _lock(directory):
        state = _read_journal(directory)
        if state is None:
            # A missing journal with existing backups must not start a new run.
            if list(directory.glob("backup-*.dpapi")):
                raise SimplifiError("Journal missing but backups exist; manual recovery required")
            state = {
                "version": 1,
                "sequence": 0,
                "previous": None,
                "plan_digest": confirmation,
                "steps": [{"status": "pending", "result": None} for _ in plan["operations"]],
            }
            save_encrypted(directory / "checkpoint-00000000.dpapi", state)
        if state.get("plan_digest") != confirmation or len(state["steps"]) != len(
            plan["operations"]
        ):
            raise ReadOnlyViolation("Journal belongs to a different plan")
        ops = plan["operations"]
        for index, (op, step) in enumerate(zip(ops, state["steps"])):
            if step["status"] in {"dispatched", "verified"}:
                backup_path = directory / f"backup-{index:08d}.dpapi"
                if not backup_path.exists() or load_encrypted(backup_path) != {
                    "plan_digest": confirmation,
                    "operation": op,
                }:
                    raise SimplifiError(
                        "Execution backup missing or changed; manual recovery required"
                    )

        def reconcile():
            nonlocal state
            current = _rules(client)
            expected = copy.deepcopy(plan["baseline"])
            changed = False
            # Check every rule, including untouched rules and completed operations.
            for op, step in zip(ops, state["steps"]):
                rid = op["rule_id"]
                if step["status"] == "dispatched":
                    if not _matches(op, current.get(rid)):
                        raise SimplifiError(
                            "Dispatched outcome unresolved; no automatic retry. Inspect server state."
                        )
                    step.update(status="verified", result=current.get(rid))
                    changed = True
                elif step["status"] not in {"pending", "verified"}:
                    raise SimplifiError("Invalid journal operation state")
                if step["status"] == "verified":
                    if step["result"] is None:
                        expected.pop(rid, None)
                    else:
                        expected[rid] = step["result"]
            if current != expected:
                raise SimplifiError(
                    "Rules changed outside this plan; stop and inspect before continuing"
                )
            if changed:
                state = _checkpoint(directory, state)

        reconcile()
        for index, op in enumerate(ops):
            if state["steps"][index]["status"] == "verified":
                continue
            reconcile()
            if op["action"] == "update":
                changes = {
                    k: op["payload"].get(k)
                    for k in EDITABLE
                    if op["payload"].get(k) != op["original"].get(k)
                }
                preview = preview_rule(client, op["original"], changes)
                if digest(preview) != digest(op["preview"]):
                    raise SimplifiError(
                        "Preview changed; prepare a new plan for remaining operations"
                    )
            reconcile()  # Preview must not hide concurrent rule edits.
            backup = {"plan_digest": confirmation, "operation": op}
            backup_path = directory / f"backup-{index:08d}.dpapi"
            if backup_path.exists():
                if load_encrypted(backup_path) != backup:
                    raise SimplifiError("Existing operation backup differs from this plan")
            else:
                save_encrypted(backup_path, backup)
            state["steps"][index]["status"] = "dispatched"
            state = _checkpoint(directory, state)  # Durable before network dispatch.
            _request(
                client,
                "DELETE" if op["action"] == "delete" else "PUT",
                f"/transaction-rules/{op['rule_id']}",
                op["payload"],
            )
            reconcile()
        reconcile()
    return change_plan_status(directory)


def prepare_rule_deletion(client, rule_id, *, plan_path):
    """Preview a single deletion as a normal encrypted change plan."""
    return prepare_change_plan(
        client, [{"action": "delete", "rule_id": rule_id}], plan_path=plan_path
    )


def apply_rule_deletion(client, plan_path, *, confirmation, journal_dir):
    """Execute/resume only a single-delete plan, using the shared journal workflow."""
    plan = load_encrypted(Path(plan_path))
    _validate(plan)
    if len(plan["operations"]) != 1 or plan["operations"][0]["action"] != "delete":
        raise ReadOnlyViolation("A single-delete plan is required")
    return execute_change_plan(
        client, plan_path, confirmation=confirmation, journal_dir=journal_dir
    )
