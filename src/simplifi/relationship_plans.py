"""Scoped transfer-pair and recurring unlink plans; no money movement or creates."""

from __future__ import annotations

import copy
import re
from decimal import Decimal
from pathlib import Path
from typing import Literal

import requests
from pydantic import Field, model_validator

from simplifi.category_update import _FIELDS, EXCLUSION_FIELDS, verify_correction
from simplifi.constants import APP_BUILD, APP_RELEASE, BASE_URL, CLIENT_ID
from simplifi.errors import ReadOnlyViolation, SimplifiError, SimplifiHTTPError
from simplifi.journal_store import JournalStore
from simplifi.review_models import Review
from simplifi.reviews import load_review, now, replace_encrypted, resolve_category
from simplifi.rule_plan_models import OperationState, StrictModel
from simplifi.secure_session import SecureSession
from simplifi.transaction_rules import digest, load_encrypted, save_encrypted


def _records(rows):
    active = [r for r in rows if not r.get("isDeleted")]
    result = {str(r["id"]): r for r in active}
    if len(result) != len(active):
        raise SimplifiError("Duplicate resource IDs")
    return result


def _eligible(t):
    if (
        t.get("type") != "CASH_FLOW"
        or t.get("split")
        or t.get("isDeleted")
        or not t.get("accountId")
        or not re.fullmatch(r"[A-Za-z0-9_-]+", str(t.get("id", "")))
    ):
        raise ReadOnlyViolation("Select existing unsplit cash-flow transactions")
    amount = t.get("amount")
    if type(amount) not in (int, float) or not Decimal(str(amount)).is_finite():
        raise ReadOnlyViolation("Invalid amount")


def _payloads(action, originals, targets):
    if not isinstance(targets, dict) or any(not isinstance(v, dict) for v in targets.values()):
        raise ReadOnlyViolation("Targets must map transaction IDs to explicit classifications")
    for t in originals:
        _eligible(t)
    if action == "unlink_recurring":
        t = originals[0]
        if (
            len(originals) != 1
            or targets
            or not t.get("stModelId")
            or t.get("transfer")
            or (t.get("coa") or {}).get("type") == "ACCOUNT"
            or not (t.get("cpData") or {}).get("id")
            or t.get("source") in {"SCHEDULED_TRANSACTION", "SCHEDULED_TRANSACTION_PENDING"}
        ):
            raise ReadOnlyViolation("Select one bank transaction with a specific recurring series")
        return []
    if len(originals) != 2:
        raise ReadOnlyViolation("Transfer operations require exactly two existing records")
    a, b = originals
    if (
        a["id"] == b["id"]
        or a["accountId"] == b["accountId"]
        or Decimal(str(a["amount"])) + Decimal(str(b["amount"])) != 0
        or a["amount"] == 0
        or any(t.get("stModelId") for t in originals)
    ):
        raise ReadOnlyViolation("Transfers need opposite nonzero amounts in distinct accounts")
    if action == "link":
        if targets or any(
            t.get("transfer")
            or (t.get("coa") or {}).get("type")
            not in {"CATEGORY", "UNCATEGORIZED", "BALANCE_ADJUSTMENT"}
            for t in originals
        ):
            raise ReadOnlyViolation("Select unlinked records without account classifications")
        if any(
            (t.get("coa") or {}).get("type") == "UNCATEGORIZED"
            and t["coa"] != {"type": "UNCATEGORIZED", "id": "0"}
            for t in originals
        ):
            raise ReadOnlyViolation("Unknown uncategorized classification")
        if any(
            (t.get("coa") or {}).get("type") == "BALANCE_ADJUSTMENT"
            and t["coa"] != {"type": "BALANCE_ADJUSTMENT", "id": "2"}
            for t in originals
        ):
            raise ReadOnlyViolation("Unsupported balance adjustment")
    elif set(targets) != {str(a["id"]), str(b["id"])} or any(
        t.get("transfer") != {"id": other["id"]}
        or t.get("coa") != {"type": "ACCOUNT", "id": other["accountId"]}
        for t, other in ((a, b), (b, a))
    ):
        raise ReadOnlyViolation("Unlink requires a reciprocal pair and explicit targets for both")
    payloads = []
    for t, other in ((a, b), (b, a)):
        p = {k: copy.deepcopy(v) for k, v in t.items() if k in _FIELDS}
        p["type"] = "CASH_FLOW"
        if action == "link":
            p.update(
                coa={"type": "ACCOUNT", "id": other["accountId"]}, transfer={"id": other["id"]}
            )
        else:
            coa = targets[str(t["id"])]
            if set(coa) != {"type", "id"} or not (
                coa["type"] == "CATEGORY"
                and bool(coa["id"])
                or coa == {"type": "UNCATEGORIZED", "id": "0"}
                or coa == {"type": "BALANCE_ADJUSTMENT", "id": "2"}
            ):
                raise ReadOnlyViolation("Unsupported unlink target")
            p.update(coa=copy.deepcopy(coa), transfer=None)
        for flag in EXCLUSION_FIELDS:
            value = t.get(flag, False)
            if type(value) is not bool:
                raise ReadOnlyViolation("Unknown exclusion state")
            p[flag] = value
        payloads.append(p)
    return payloads


class RelationshipOperation(StrictModel):
    action: Literal["link", "unlink", "unlink_recurring"]
    originals: list[dict] = Field(min_length=1, max_length=2)
    targets: dict[str, dict] = Field(default_factory=dict)
    payloads: list[dict]

    @model_validator(mode="after")
    def bound(self):
        if digest(self.payloads) != digest(_payloads(self.action, self.originals, self.targets)):
            raise ValueError("Relationship payload differs from selected operation")
        return self


class RelationshipPlan(StrictModel):
    version: Literal[1] = 1
    kind: Literal["transaction-relationship-plan"] = "transaction-relationship-plan"
    dataset_id: str = Field(min_length=1)
    review_id: str = Field(min_length=1)
    baseline: dict[str, dict]
    accounts: dict[str, dict]
    series: dict[str, dict]
    categories: list[dict]
    operations: list[RelationshipOperation] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def originals_in_baseline(self):
        for t in self.operations[0].originals:
            if self.baseline.get(str(t["id"])) != t:
                raise ValueError("Original differs from baseline")
        return self


def prepare_relationship_plan(
    client, review_dir, *, action, transaction_ids, plan_path, targets=None
):
    review = load_review(review_dir)
    if review.current.dataset_id != client.dataset_id:
        raise ReadOnlyViolation("Dataset differs from review")
    if len(transaction_ids) != len(set(transaction_ids)) or not transaction_ids:
        raise ReadOnlyViolation("Select unique transaction IDs")
    baseline = _records(client.transactions())
    if not set(transaction_ids) <= baseline.keys():
        raise ReadOnlyViolation("Selected transaction missing from API snapshot")
    cached_ids = {str(t["id"]) for t in review.current.transactions}
    if not set(transaction_ids) <= cached_ids:
        raise ReadOnlyViolation("Refresh selected records into the review first")
    originals = [client.transaction(tid) for tid in transaction_ids]
    cached = {str(t["id"]): t for t in review.current.transactions}
    if any(cached.get(str(t["id"])) != t for t in originals):
        raise SimplifiError("Selected review evidence is stale; refresh and review before planning")
    payloads = _payloads(action, originals, targets or {})
    categories = client.categories()
    for coa in (targets or {}).values():
        if coa.get("type") == "CATEGORY":
            resolve_category(categories, category_id=str(coa["id"]))
    all_accounts = _records(client.accounts())
    account_ids = {str(t["accountId"]) for t in originals}
    if not account_ids <= all_accounts.keys():
        raise ReadOnlyViolation("Unknown account")
    accounts = {aid: all_accounts[aid] for aid in account_ids}
    if action != "unlink_recurring":
        currencies = {a.get("currency") or a.get("currencyCode") for a in accounts.values()}
        if None in currencies or len(currencies) != 1:
            raise ReadOnlyViolation("Transfer account currencies must be known and equal")
    series = _records(client.scheduled_transactions()) if action == "unlink_recurring" else {}
    if action == "unlink_recurring" and str(originals[0]["stModelId"]) not in series:
        raise ReadOnlyViolation("Recurring series missing")
    plan = RelationshipPlan(
        dataset_id=client.dataset_id,
        review_id=review.id,
        baseline=baseline,
        accounts=accounts,
        series=series,
        categories=categories,
        operations=[
            RelationshipOperation(
                action=action, originals=originals, targets=targets or {}, payloads=payloads
            )
        ],
    )
    if _records(client.transactions()) != baseline:
        raise SimplifiError("Transactions changed during preparation")
    if client.categories() != categories or (
        action == "unlink_recurring" and _records(client.scheduled_transactions()) != series
    ):
        raise SimplifiError("Definitions changed during preparation")
    save_encrypted(Path(plan_path), plan.to_wire())
    return inspect_relationship_plan(plan_path)


def _load(path):
    return RelationshipPlan.model_validate(load_encrypted(Path(path)))


def inspect_relationship_plan(path):
    plan = _load(path)
    op = plan.operations[0]
    return {
        "confirmation": digest(plan.to_wire()),
        "dataset_id": plan.dataset_id,
        "action": op.action,
        "originals": op.originals,
        "payloads": op.payloads,
        "series_id": op.originals[0].get("stModelId") if op.action == "unlink_recurring" else None,
        "preserve_exclusions": True,
        "creates_counterparts": False,
    }


class RelationshipSession(SecureSession):
    def __init__(self, operation):
        super().__init__()
        op = RelationshipOperation.model_validate(operation.to_wire())
        if op.action == "unlink_recurring":
            tid = op.originals[0]["id"]
            self.url = BASE_URL + f"/transactions/{tid}/unlink-to-scheduled-txn"
            self.payload = {"action": "unlink-to-scheduled-txn", "id": tid}
        else:
            self.url = BASE_URL + "/transactions"
            self.payload = {"resources": op.payloads}
        self.used = False

    def request(self, method, url, **kwargs):
        if (
            self.used
            or method != "PUT"
            or url != self.url
            or digest(kwargs.get("json")) != digest(self.payload)
            or kwargs.get("params")
            or kwargs.get("data")
        ):
            raise ReadOnlyViolation("Request differs from the approved relationship operation")
        self.used = True
        kwargs.update(allow_redirects=False, verify=True, timeout=60)
        try:
            response = requests.Session.request(self, method, url, **kwargs)
        except requests.RequestException:
            raise SimplifiError(
                "Relationship outcome uncertain; reconcile before any retry"
            ) from None
        if not 200 <= response.status_code < 300:
            raise SimplifiHTTPError(
                response.status_code, "relationship update", "Response suppressed"
            )
        return response


def _send(client, op):
    with RelationshipSession(op) as session:
        session.request(
            "PUT",
            session.url,
            json=session.payload,
            headers={
                "Authorization": f"Bearer {client.auth.ensure_access_token()}",
                "Qcs-Dataset-Id": client.dataset_id,
                "app-client-id": CLIENT_ID,
                "app-release": APP_RELEASE,
                "app-build": APP_BUILD,
                "accept": "application/json",
            },
        )


def _verify_results(op, results):
    if not isinstance(results, dict) or set(results) != {str(t["id"]) for t in op.originals}:
        raise SimplifiError("Relationship results missing")
    if op.action != "unlink_recurring":
        for original, payload in zip(op.originals, op.payloads):
            result = results[str(original["id"])]
            # The frontend normalizes an absent note; never allow loss of note content.
            before = copy.deepcopy(original)
            if before.get("memo") is None and result.get("memo") == "":
                before["memo"] = ""
            verify_correction(
                before, payload, result, allowed_changes={"coa", "transfer"} | EXCLUSION_FIELDS
            )
    else:
        original = op.originals[0]
        result = results[str(original["id"])]
        if (
            not result.get("id")
            or result.get("stModelId")
            or result.get("stDueOn")
            or result.get("isBill")
            or result.get("isSubscription")
            or result.get("isDeleted")
        ):
            raise SimplifiError("Recurring association remains or record was deleted")
        if any(
            result.get(k) is not None and result.get(k) is not False
            for k in ("isBill", "isSubscription")
        ):
            raise SimplifiError("Unexpected recurring flags")
        if (result.get("cpData") or {}).get("id") != original["cpData"]["id"]:
            raise SimplifiError("Replacement bank identity differs")
        payload = {
            **original,
            "id": result["id"],
            "stModelId": None,
            "stDueOn": None,
            "isBill": False,
            "isSubscription": False,
        }
        normalized = {
            **result,
            "isBill": bool(result.get("isBill")),
            "isSubscription": bool(result.get("isSubscription")),
        }
        verify_correction(
            original,
            payload,
            normalized,
            allowed_changes={"id", "stModelId", "stDueOn", "isBill", "isSubscription"},
        )


class RelationshipJournal(JournalStore):
    def verify_result(self, operation, result):
        _verify_results(operation, result)


def _reconcile(plan, current):
    op = plan.operations[0]
    if op.action == "unlink_recurring":
        original = op.originals[0]
        old_id = str(original["id"])
        if old_id in current:
            candidates = [current[old_id]]
        else:
            candidates = [
                t
                for tid, t in current.items()
                if tid not in plan.baseline
                and (t.get("cpData") or {}).get("id") == original["cpData"]["id"]
            ]
        if len(candidates) != 1:
            raise SimplifiError("Recurring replacement missing or ambiguous; no retry")
        results = {old_id: candidates[0]}
    else:
        results = {str(t["id"]): current.get(str(t["id"])) for t in op.originals}
        if any(t is None for t in results.values()):
            raise SimplifiError("Transfer counterpart missing; no retry")
    _verify_results(op, results)
    expected = copy.deepcopy(plan.baseline)
    for old_id, result in results.items():
        expected.pop(old_id)
        expected[str(result["id"])] = result
    if current != expected:
        raise SimplifiError("Unrelated records changed or a counterpart was created; no retry")
    return results


def _project(review_dir, plan, results, confirmation):
    with JournalStore(review_dir).locked():
        review = load_review(review_dir)
        if review.id != plan.review_id or review.current.dataset_id != plan.dataset_id:
            raise ReadOnlyViolation("Relationship plan belongs to a different review")
        if any(h.get("plan_digest") == confirmation for h in review.history):
            return
        wire = review.to_wire()
        wire["current"]["transactions"] = [
            results.get(str(t["id"]), t) for t in review.current.transactions
        ]
        for old_id, result in results.items():
            if str(result["id"]) != old_id:
                # Track the original review ID even after a previous established replacement.
                source_ids = [k for k, v in review.replacements.items() if v == old_id] or [old_id]
                for source_id in source_ids:
                    wire["replacements"][source_id] = str(result["id"])
        wire["history"].append(
            {
                "kind": "execution",
                "plan_digest": confirmation,
                "verified_at": now(),
                "outcomes": {str(r["id"]): "changed" for r in results.values()},
                "results": results,
                "relationship_plan": plan.to_wire(),
            }
        )
        replace_encrypted(Path(review_dir) / "review.dpapi", Review.model_validate(wire).to_wire())


def execute_relationship_plan(client, path, *, confirmation, journal_dir, review_dir):
    plan = _load(path)
    if digest(plan.to_wire()) != confirmation or plan.dataset_id != client.dataset_id:
        raise ReadOnlyViolation("Relationship digest or dataset differs")
    journal = RelationshipJournal(journal_dir)
    with journal.locked(), JournalStore(Path(review_dir) / "execution").locked():
        review = load_review(review_dir)
        if review.id != plan.review_id or review.current.dataset_id != plan.dataset_id:
            raise ReadOnlyViolation("Relationship plan belongs to a different review")
        state = journal.start(plan, confirmation)
        op = plan.operations[0]
        accounts = _records(client.accounts())
        if any(accounts.get(aid) != a for aid, a in plan.accounts.items()):
            raise SimplifiError("Selected account definitions changed")
        if (
            op.action == "unlink_recurring"
            and _records(client.scheduled_transactions()) != plan.series
        ):
            raise SimplifiError("Recurring series changed")
        if client.categories() != plan.categories:
            raise SimplifiError("Category definitions changed")
        current = _records(client.transactions())
        if state.steps[0].status == OperationState.PENDING:
            cached = {str(t["id"]): t for t in review.current.transactions}
            if any(cached.get(str(t["id"])) != t for t in op.originals):
                raise SimplifiError("Review evidence changed since planning")
            if current != plan.baseline or any(
                client.transaction(str(t["id"])) != t for t in op.originals
            ):
                raise SimplifiError("Originals changed; prepare a new relationship plan")
            journal.save_backup(0, op, confirmation)
            state = journal.checkpoint(state.replace_step(0, OperationState.DISPATCHED))
            # The same exact records are rechecked immediately before the single request.
            if any(client.transaction(str(t["id"])) != t for t in op.originals):
                raise SimplifiError(
                    "Original changed immediately before dispatch; reconcile, no retry"
                )
            _send(client, op)
            current = _records(client.transactions())
        results = _reconcile(plan, current)
        if (
            op.action == "unlink_recurring"
            and _records(client.scheduled_transactions()) != plan.series
        ):
            raise SimplifiError("Recurring series changed during unlink; inspect before continuing")
        if state.steps[0].status == OperationState.DISPATCHED:
            state = journal.checkpoint(state.replace_step(0, OperationState.VERIFIED, results))
        elif state.steps[0].result != results:
            raise SimplifiError("Previously verified relationship results changed")
        _project(review_dir, plan, results, confirmation)
    return journal.status()
