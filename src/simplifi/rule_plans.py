"""Typed future-only rule planning and execution; persistence is separate."""

from __future__ import annotations

import copy
from pathlib import Path

from simplifi.errors import ReadOnlyViolation, SimplifiError
from simplifi.journal_store import JournalStore
from simplifi.rule_plan_models import (
    ChangePlan,
    DeleteOperation,
    OperationState,
    UpdateOperation,
    UpdateRequest,
    parse_requests,
)
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


def _load_plan(path):
    return ChangePlan.from_wire(load_encrypted(Path(path)))


def inspect_change_plan(plan_path):
    """Return a reviewable diff and confirmation digest without authenticating."""
    from simplifi.rule_preview import preview_warnings

    plan = _load_plan(plan_path)
    operations = []
    for op in plan.operations:
        row = {"action": op.action, "rule_id": op.rule_id}
        if isinstance(op, DeleteOperation):
            row["original"] = op.original
        else:
            row["changes"] = {
                k: {"before": op.original.get(k), "after": op.payload.get(k)}
                for k in sorted(op.changes)
            }
            row["preview"] = {
                k: op.preview.get(k)
                for k in ("srcTxnsCount", "resultTxnsCount", "previewTxnsEndDate")
            }
            row["preview"].update(sample_count=len(op.preview["transactions"]), exhaustive=False)
            row["preview"]["warnings"] = preview_warnings(op.payload)
        operations.append(row)
    return {
        "confirmation": digest(plan.to_wire()),
        "apply_to_existing": False,
        "operations": operations,
    }


def prepare_change_plan(client, operations, *, plan_path):
    """Validate all commands before previews; preserve version-1 plan serialization."""
    if not client.dataset_id:
        raise SimplifiError("Resolve a dataset before planning rule changes")
    requests = parse_requests(operations)
    baseline = _rules(client)
    selected = []
    for request in requests:
        if request.rule_id not in baseline:
            raise ReadOnlyViolation("Select an existing rule ID")
        original = copy.deepcopy(baseline[request.rule_id])
        if original.get("isDeleted"):
            raise ReadOnlyViolation("An existing active rule is required")
        payload = None
        if isinstance(request, UpdateRequest):
            try:
                payload = rule_payload(original, request.changes)
            except (TypeError, AttributeError, ValueError):
                raise ReadOnlyViolation("Invalid rule changes; details suppressed") from None
        selected.append((request, original, payload))
    planned = []
    for request, original, payload in selected:
        if isinstance(request, UpdateRequest):
            preview = preview_rule(client, original, request.changes)
            op = UpdateOperation(
                action="update",
                rule_id=request.rule_id,
                original=original,
                payload=payload,
                preview=preview,
            )
        else:
            op = DeleteOperation(
                action="delete",
                rule_id=request.rule_id,
                original=original,
                payload=None,
                preview=None,
            )
        planned.append(op)
    if _rules(client) != baseline:
        raise SimplifiError("Rules changed during planning; prepare a new plan")
    plan = ChangePlan(
        version=1,
        kind="rule-change-plan",
        dataset_id=client.dataset_id,
        baseline=baseline,
        operations=planned,
    )
    save_encrypted(Path(plan_path), plan.to_wire())
    return inspect_change_plan(plan_path)


def change_plan_status(journal_dir):
    """Read local status without constructing an authenticated API client."""
    return JournalStore(journal_dir).status()


def _matches(op, current):
    if isinstance(op, DeleteOperation):
        return current is None
    return current is not None and all(current.get(k) == op.payload.get(k) for k in COMPARE)


def reconcile_state(plan, state, current):
    """Pure transition: validate every rule before acknowledging a result."""
    expected = copy.deepcopy(plan.baseline)
    updated = state
    for index, (op, step) in enumerate(zip(plan.operations, state.steps)):
        if step.status == OperationState.DISPATCHED:
            if not _matches(op, current.get(op.rule_id)):
                raise SimplifiError(
                    "Dispatched outcome unresolved; no automatic retry. Inspect server state."
                )
            updated = updated.replace_step(
                index, OperationState.VERIFIED, copy.deepcopy(current.get(op.rule_id))
            )
            step = updated.steps[index]
        if step.status == OperationState.VERIFIED:
            if not _matches(op, step.result):
                raise SimplifiError("Journal result differs from the approved operation")
            if step.result is None:
                expected.pop(op.rule_id, None)
            else:
                expected[op.rule_id] = step.result
    if current != expected:
        raise SimplifiError("Rules changed outside this plan; stop and inspect before continuing")
    return updated


def _reconcile(client, plan, state, journal):
    updated = reconcile_state(plan, state, _rules(client))
    return journal.checkpoint(updated) if updated is not state else state


def execute_change_plan(client, plan_path, *, confirmation, journal_dir, journal_store=None):
    """Execute/resume; dispatched writes are read/reconciled, never replayed."""
    plan = _load_plan(plan_path)
    if digest(plan.to_wire()) != confirmation or plan.dataset_id != client.dataset_id:
        raise ReadOnlyViolation("Confirmation or dataset differs from the rule plan")
    journal = journal_store if journal_store is not None else JournalStore(journal_dir)
    if journal.directory != Path(journal_dir):
        raise ReadOnlyViolation("Journal store directory differs from the selected journal")
    with journal.locked():
        state = journal.start(plan, confirmation)
        state = _reconcile(client, plan, state, journal)
        for index, op in enumerate(plan.operations):
            if state.steps[index].status == OperationState.VERIFIED:
                continue
            state = _reconcile(client, plan, state, journal)
            if isinstance(op, UpdateOperation):
                preview = preview_rule(client, op.original, op.changes)
                if digest(preview) != digest(op.preview):
                    raise SimplifiError(
                        "Preview changed; prepare a new plan for remaining operations"
                    )
            state = _reconcile(client, plan, state, journal)
            journal.save_backup(index, op, confirmation)
            state = journal.checkpoint(state.replace_step(index, OperationState.DISPATCHED))
            _request(
                client,
                "DELETE" if isinstance(op, DeleteOperation) else "PUT",
                f"/transaction-rules/{op.rule_id}",
                op.payload,
            )
            state = _reconcile(client, plan, state, journal)
        _reconcile(client, plan, state, journal)
    return journal.status()


def prepare_rule_deletion(client, rule_id, *, plan_path):
    return prepare_change_plan(
        client, [{"action": "delete", "rule_id": rule_id}], plan_path=plan_path
    )


def apply_rule_deletion(client, plan_path, *, confirmation, journal_dir):
    plan = _load_plan(plan_path)
    if len(plan.operations) != 1 or not isinstance(plan.operations[0], DeleteOperation):
        raise ReadOnlyViolation("A single-delete plan is required")
    return execute_change_plan(
        client, plan_path, confirmation=confirmation, journal_dir=journal_dir
    )
