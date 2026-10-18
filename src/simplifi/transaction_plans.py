"""Typed, explicitly approved category plans and conservative resumable execution."""

from __future__ import annotations

from pathlib import Path

from simplifi.category_update import category_payload, update_category, verify_correction
from simplifi.errors import ReadOnlyViolation, SimplifiError
from simplifi.journal_store import JournalStore
from simplifi.review_models import Review, TransactionOperation, TransactionPlan
from simplifi.reviews import load_review, now, replace_encrypted, resolve_category
from simplifi.rule_plan_models import OperationState
from simplifi.transaction_rules import digest, load_encrypted, save_encrypted


def _load(path):
    try:
        return TransactionPlan.model_validate(load_encrypted(Path(path)))
    except (ValueError, TypeError, KeyError):
        raise ReadOnlyViolation("Invalid encrypted transaction plan; details suppressed") from None


def prepare_transaction_plan(client, review_dir, *, plan_path, decision_ids=None):
    review = load_review(review_dir)
    if not client.dataset_id or review.current.dataset_id != client.dataset_id:
        raise ReadOnlyViolation("Dataset differs from review")
    selected = [d for d in review.decisions if decision_ids is None or d.id in decision_ids]
    if decision_ids is not None and set(decision_ids) != {d.id for d in selected}:
        raise ReadOnlyViolation("Unknown decision selection")
    categories = client.categories()
    requests = {}
    for d in selected:
        if d.action not in {"category", "confirm"}:
            continue
        for old_id in d.transaction_ids:
            tid = review.replacements.get(old_id, old_id)
            category_id = d.exceptions.get(old_id, d.category_id)
            target = resolve_category(categories, category_id=category_id) if category_id else None
            previous = requests.get(tid)
            if previous and (previous["action"], previous["category"]) != (d.action, target):
                raise ReadOnlyViolation("Conflicting decisions for an overlapping transaction")
            if previous:
                previous["decision_ids"].append(d.id)
            else:
                requests[tid] = {"action": d.action, "category": target, "decision_ids": [d.id]}
    if not requests:
        raise ReadOnlyViolation("No category changes or confirmations selected")
    operations = []
    cached = {str(t["id"]): t for t in review.current.transactions}
    for tid, request in requests.items():
        original = client.transaction(tid)
        if request["action"] == "confirm" and original != cached.get(tid):
            raise SimplifiError("Confirmation changed since review; refresh and review it again")
        operations.append(
            TransactionOperation(
                **request,
                transaction_id=tid,
                original=original,
                payload=category_payload(original, request["category"])
                if request["category"]
                else None,
            )
        )
    # Reject drift during preparation as well as during execution.
    if categories != client.categories() or any(
        client.transaction(op.transaction_id) != op.original for op in operations
    ):
        raise SimplifiError("Selected data changed during preparation")
    plan = TransactionPlan(
        review_id=review.id, dataset_id=client.dataset_id, decisions=selected, operations=operations
    )
    save_encrypted(Path(plan_path), plan.to_wire())
    return inspect_transaction_plan(plan_path)


def inspect_transaction_plan(plan_path):
    plan = _load(plan_path)
    return {
        "confirmation": digest(plan.to_wire()),
        "dataset_id": plan.dataset_id,
        "operations": [
            {
                "transaction_id": op.transaction_id,
                "action": op.action,
                "before": op.original.get("coa"),
                "after": op.payload["coa"] if op.payload else op.original.get("coa"),
                "amount": op.original.get("amount"),
                "already_correct": bool(op.payload and op.original.get("coa") == op.payload["coa"]),
            }
            for op in plan.operations
        ],
    }


class TransactionJournal(JournalStore):
    def verify_result(self, operation, result):
        if result is None:
            raise SimplifiError("Verified transaction result missing")
        _verify(operation, result)


def _verify(op, current):
    if op.action == "confirm":
        if current != op.original:
            raise SimplifiError("Confirmed transaction changed")
    else:
        verify_correction(op.original, op.payload, current, allowed_changes={"coa"})


def _reconcile(client, plan, state, journal):
    updated = state
    # Preflight every selected record before acknowledging or dispatching writes.
    for index, (op, step) in enumerate(zip(plan.operations, state.steps)):
        current = client.transaction(op.transaction_id)
        if step.status == OperationState.PENDING:
            if current != op.original:
                raise SimplifiError("Selected transaction changed; prepare a new plan")
        else:
            _verify(op, current)
            if step.status == OperationState.VERIFIED:
                if current != step.result:
                    raise SimplifiError("Previously verified transaction changed")
            else:
                updated = updated.replace_step(index, OperationState.VERIFIED, current)
    return journal.checkpoint(updated) if updated is not state else state


def _project(review_dir, plan, state, confirmation):
    """Rebuild local current state from durable receipts, never by repeating writes."""
    with JournalStore(review_dir).locked():
        review = load_review(review_dir)
        _validate_review(plan, review)
        if any(
            h.get("plan_digest") == confirmation and h.get("kind") == "execution"
            for h in review.history
        ):
            return
        results = {op.transaction_id: step.result for op, step in zip(plan.operations, state.steps)}
        old = {str(t["id"]) for t in review.current.transactions}
        if not results.keys() <= old:
            raise SimplifiError("Review is missing planned transaction IDs")
        wire = review.to_wire()
        wire["current"]["transactions"] = [
            results.get(str(t["id"]), t) for t in review.current.transactions
        ]
        categories = {str(c["id"]): c for c in review.current.categories}
        categories.update(
            {str(op.category["id"]): op.category for op in plan.operations if op.category}
        )
        wire["current"]["categories"] = list(categories.values())
        wire["context"]["last_verified_at"] = now()
        outcomes = {}
        for op in plan.operations:
            if op.action == "confirm":
                outcome = "confirmed"
            elif op.original.get("coa") == op.payload["coa"]:
                outcome = "already_correct"
            else:
                outcome = "changed"
            outcomes[op.transaction_id] = outcome
        wire["history"].append(
            {
                "kind": "execution",
                "plan_digest": confirmation,
                "verified_at": now(),
                "outcomes": outcomes,
                "results": results,
            }
        )
        replace_encrypted(Path(review_dir) / "review.dpapi", Review.model_validate(wire).to_wire())


def _validate_review(plan, review):
    if review.id != plan.review_id or review.current.dataset_id != plan.dataset_id:
        raise ReadOnlyViolation("Plan belongs to a different review")
    # Do not write against edited decisions even if the old plan remains valid.
    decisions = {d.id: d.to_wire() for d in review.decisions}
    if any(decisions.get(d.id) != d.to_wire() for d in plan.decisions):
        raise ReadOnlyViolation("Approved decisions changed")


def execute_transaction_plan(client, plan_path, *, confirmation, journal_dir, review_dir):
    plan = _load(plan_path)
    if digest(plan.to_wire()) != confirmation or plan.dataset_id != client.dataset_id:
        raise ReadOnlyViolation("Confirmation or dataset differs from the transaction plan")
    journal = TransactionJournal(journal_dir)
    with journal.locked(), JournalStore(Path(review_dir) / "execution").locked():
        _validate_review(plan, load_review(review_dir))
        state = journal.start(plan, confirmation)
        categories = client.categories()
        for op in plan.operations:
            if (
                op.category
                and resolve_category(categories, category_id=str(op.category["id"])) != op.category
            ):
                raise SimplifiError("Target category changed; prepare a new plan")
        state = _reconcile(client, plan, state, journal)
        for index, op in enumerate(plan.operations):
            if state.steps[index].status == OperationState.VERIFIED:
                continue
            journal.save_backup(index, op, confirmation)
            state = journal.checkpoint(state.replace_step(index, OperationState.DISPATCHED))
            if op.action == "category":
                update_category(
                    client,
                    op.original,
                    op.category,
                    backup_path=Path(journal_dir) / f"write-{index:08d}.dpapi",
                )
            state = _reconcile(client, plan, state, journal)
        _project(review_dir, plan, state, confirmation)
    return journal.status()


def transaction_plan_status(journal_dir):
    return TransactionJournal(journal_dir).status()
