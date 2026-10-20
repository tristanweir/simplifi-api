"""Future-only rule creation with stable client identity and no uncertain replay."""

import copy
import re
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from simplifi.errors import ReadOnlyViolation, SimplifiError
from simplifi.journal_store import JournalStore
from simplifi.reviews import resolve_category
from simplifi.rule_plan_models import OperationState, StrictModel
from simplifi.rule_plans import _rules
from simplifi.transaction_rules import (
    EDITABLE,
    PREVIEW_PATH,
    _request,
    digest,
    load_encrypted,
    rule_payload,
    save_encrypted,
)

CONDITIONS = {
    "payeeSourceCondition",
    "amountSourceCondition",
    "coaSourceConditions",
    "accountSourceCondition",
    "usageSourceType",
}


def creation_payload(changes, *, client_id):
    if (
        not isinstance(changes, dict)
        or not changes
        or not changes.keys() <= EDITABLE
        or not isinstance(client_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", client_id)
    ):
        raise ReadOnlyViolation("Creation requires editable rule fields and a stable client ID")
    # Reuse field validation, then remove the validation-only identity before transport.
    payload = rule_payload({"id": client_id}, changes)
    payload.pop("id")
    payload["clientId"] = client_id
    return payload


class CreateRuleOperation(StrictModel):
    action: Literal["create"] = "create"
    payload: dict
    preview: dict

    @model_validator(mode="after")
    def bound(self):
        changes = {
            k: v
            for k, v in self.payload.items()
            if k not in {"clientId", "applyToExistingTransactions"}
        }
        if self.payload.get(
            "applyToExistingTransactions"
        ) is not False or self.payload != creation_payload(
            changes, client_id=self.payload.get("clientId")
        ):
            raise ValueError("Invalid creation payload")
        if not isinstance(self.preview.get("transactions"), list):
            raise TypeError("Rule preview missing")
        return self


class RuleCreationPlan(StrictModel):
    version: Literal[1] = 1
    kind: Literal["rule-creation-plan"] = "rule-creation-plan"
    dataset_id: str = Field(min_length=1)
    baseline: dict[str, dict]
    transaction_baseline: dict[str, dict]
    categories: list[dict]
    account_ids: list[str]
    operations: list[CreateRuleOperation] = Field(min_length=1, max_length=1)


def _preview(client, payload):
    result = _request(client, "POST", PREVIEW_PATH, payload)
    if not isinstance(result, dict) or not isinstance(result.get("transactions"), list):
        raise SimplifiError("Unsupported creation preview response")
    return result


def _transactions(client):
    rows = client.transactions()
    records = {str(t["id"]): copy.deepcopy(t) for t in rows}
    if len(records) != len(rows):
        raise SimplifiError("Duplicate transaction IDs")
    return records


def _validate_references(payload, categories, accounts):
    for field in ("coaTargetCondition", "businessCoaTargetCondition"):
        coa = payload.get(field)
        if coa:
            if coa.get("type") != "CATEGORY":
                raise ReadOnlyViolation("Rule creation only supports category targets")
            resolve_category(categories, category_id=str(coa.get("id")))
    source = payload.get("accountSourceCondition")
    if source:
        if source.get("accountSourceType") not in {"PERSONAL", "BUSINESS", "SPECIFIC"}:
            raise ReadOnlyViolation("Unknown account match type")
        if source["accountSourceType"] == "SPECIFIC":
            ids = source.get("accountSourceIds")
            if (
                not isinstance(ids, list)
                or not ids
                or len(ids) != len(set(ids))
                or not set(ids) <= accounts
            ):
                raise ReadOnlyViolation("Specific rule accounts must exist")


def _duplicate(payload, rules):
    return any(
        not r.get("isDeleted") and all(r.get(k) == payload.get(k) for k in CONDITIONS)
        for r in rules.values()
    )


def prepare_rule_creation(client, changes, *, plan_path):
    if not client.dataset_id:
        raise ReadOnlyViolation("Resolve a dataset before creating a rule plan")
    payload = creation_payload(changes, client_id=uuid4().hex)
    baseline = _rules(client)
    if _duplicate(payload, baseline):
        raise ReadOnlyViolation("An existing rule has the same conditions; review it instead")
    categories = client.categories()
    account_ids = sorted(str(a["id"]) for a in client.accounts() if not a.get("isDeleted"))
    _validate_references(payload, categories, set(account_ids))
    transaction_baseline = _transactions(client)
    preview = _preview(client, payload)
    if _rules(client) != baseline or _transactions(client) != transaction_baseline:
        raise SimplifiError("Rules or transactions changed during preparation")
    plan = RuleCreationPlan(
        dataset_id=client.dataset_id,
        baseline=baseline,
        transaction_baseline=transaction_baseline,
        categories=categories,
        account_ids=account_ids,
        operations=[CreateRuleOperation(payload=payload, preview=preview)],
    )
    save_encrypted(Path(plan_path), plan.to_wire())
    return inspect_rule_creation(plan_path)


def _load(path):
    return RuleCreationPlan.model_validate(load_encrypted(Path(path)))


def inspect_rule_creation(path):
    from simplifi.rule_preview import preview_warnings

    plan = _load(path)
    op = plan.operations[0]
    return {
        "confirmation": digest(plan.to_wire()),
        "dataset_id": plan.dataset_id,
        "payload": op.payload,
        "preview": op.preview,
        "apply_to_existing": False,
        "warnings": preview_warnings(op.payload),
    }


def _verify_created(op, result):
    if (
        not isinstance(result, dict)
        or not result.get("id")
        or result.get("isDeleted")
        or result.get("clientId") != op.payload["clientId"]
        or any(result.get(k) != op.payload.get(k) for k in EDITABLE)
        or (
            result.get("applyToExistingTransactions") is not False
            and result.get("applyToExistingTransactions") is not None
        )
    ):
        raise SimplifiError("Created rule differs or cannot be identified safely; no retry")


def _reconcile(plan, current):
    op = plan.operations[0]
    added = set(current) - set(plan.baseline)
    if len(added) != 1 or set(plan.baseline) - set(current):
        raise SimplifiError("Creation missing, ambiguous, or unrelated rules removed; no retry")
    tid = next(iter(added))
    created = current[tid]
    _verify_created(op, created)
    previous, following = created.get("prevTransactionRuleId"), created.get("nextTransactionRuleId")
    # Only an insertion at the original adjacency is permitted, never arbitrary reordering.
    if previous is not None and (
        previous not in plan.baseline
        or plan.baseline[previous].get("nextTransactionRuleId") != following
    ):
        raise SimplifiError("Unexpected rule ordering change")
    if following is not None and (
        following not in plan.baseline
        or plan.baseline[following].get("prevTransactionRuleId") != previous
    ):
        raise SimplifiError("Unexpected rule ordering change")
    for rid, before in plan.baseline.items():
        expected = copy.deepcopy(before)
        if rid == previous:
            expected["nextTransactionRuleId"] = tid
        if rid == following:
            expected["prevTransactionRuleId"] = tid
        actual = current[rid]
        ignored = {"modifiedAt", "dbVersion"} if expected != before else set()
        if {k: v for k, v in actual.items() if k not in ignored} != {
            k: v for k, v in expected.items() if k not in ignored
        }:
            raise SimplifiError("Unrelated rule changed during creation")
    return created


class RuleCreationJournal(JournalStore):
    def verify_result(self, operation, result):
        _verify_created(operation, result)


def execute_rule_creation(client, path, *, confirmation, journal_dir):
    plan = _load(path)
    if digest(plan.to_wire()) != confirmation or plan.dataset_id != client.dataset_id:
        raise ReadOnlyViolation("Creation confirmation or dataset differs")
    journal = RuleCreationJournal(journal_dir)
    with journal.locked():
        state = journal.start(plan, confirmation)
        op = plan.operations[0]
        current = _rules(client)
        if _transactions(client) != plan.transaction_baseline:
            raise SimplifiError("Transactions changed; future-only creation cannot be verified")
        if state.steps[0].status == OperationState.PENDING:
            if current != plan.baseline or _duplicate(op.payload, current):
                raise SimplifiError("Rule baseline changed")
            categories = client.categories()
            if categories != plan.categories:
                raise SimplifiError("Categories changed")
            accounts = {str(a["id"]) for a in client.accounts() if not a.get("isDeleted")}
            _validate_references(op.payload, categories, accounts)
            if digest(_preview(client, op.payload)) != digest(op.preview):
                raise SimplifiError("Creation preview changed")
            if (
                _rules(client) != plan.baseline
                or _transactions(client) != plan.transaction_baseline
            ):
                raise SimplifiError("Rules or transactions changed immediately before creation")
            journal.save_backup(0, op, confirmation)
            state = journal.checkpoint(state.replace_step(0, OperationState.DISPATCHED))
            _request(client, "POST", "/transaction-rules", op.payload)
            current = _rules(client)
        result = _reconcile(plan, current)
        if _transactions(client) != plan.transaction_baseline:
            raise SimplifiError("Transactions changed during future-only creation; no retry")
        if state.steps[0].status == OperationState.DISPATCHED:
            journal.checkpoint(state.replace_step(0, OperationState.VERIFIED, result))
        elif state.steps[0].result != result:
            raise SimplifiError("Previously verified rule changed")
    return journal.status()
