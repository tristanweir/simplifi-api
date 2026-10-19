"""Local review queries, evidence, and read-only refresh operations."""

from pathlib import Path
from uuid import uuid4

from simplifi.errors import SimplifiError
from simplifi.journal_store import JournalStore
from simplifi.review_models import Review
from simplifi.reviews import load_review, now, record_decisions, replace_encrypted, review_summary
from simplifi.transaction_rules import digest


def show_finding(directory, number):
    review = load_review(directory)
    findings = [f for f in review.findings if f.number == number]
    if len(findings) != 1:
        raise SimplifiError("Unknown finding number")
    f = findings[0]
    original = {
        **review.context.get("added_originals", {}),
        **{str(t["id"]): t for t in review.original.transactions},
    }
    current = {str(t["id"]): t for t in review.current.transactions}
    decisions = {
        tid: d for d in review.decisions if d.finding_id == f.id for tid in d.transaction_ids
    }
    return {
        "finding": f.to_wire(),
        "transactions": [
            {
                "original_id": tid,
                "original": original.get(tid),
                "current": current.get(review.replacements.get(tid, tid)),
                "decision": decisions[tid].to_wire() if tid in decisions else None,
                "issues": [
                    i
                    for i in review.context.get("import_issues", [])
                    + review.context.get("sync_issues", [])
                    if i.get("transaction_id") in {tid, review.replacements.get(tid, tid)}
                    and i.get("finding", number) == number
                ],
            }
            for tid in f.transaction_ids
        ],
        "evidence": [
            h for h in review.history if h.get("kind") == "evidence" and h.get("finding_id") == f.id
        ],
    }


def unresolved_findings(directory):
    review = load_review(directory)
    dispositions = {
        (d.finding_id, tid): d.action for d in review.decisions for tid in d.transaction_ids
    }
    return [
        {"number": f.number, "id": f.id, "title": f.title, "transaction_ids": ids}
        for f in review.findings
        if (
            ids := [
                tid
                for tid in f.transaction_ids
                if dispositions.get((f.id, tid), "unresolved") == "unresolved"
            ]
        )
    ]


def _selection(review, number, transaction_ids):
    findings = [f for f in review.findings if f.number == number]
    if len(findings) != 1:
        raise SimplifiError("Unknown finding number")
    f = findings[0]
    if not transaction_ids or len(transaction_ids) != len(set(transaction_ids)):
        raise SimplifiError("Select nonempty unique transaction IDs")
    if not set(transaction_ids) <= set(f.transaction_ids):
        raise SimplifiError("Selection exceeds finding")
    return f


def confirm_records(directory, number, transaction_ids, *, note, decision_id=None):
    review = load_review(directory)
    f = _selection(review, number, transaction_ids)
    return record_decisions(
        directory,
        [
            {
                "id": decision_id or uuid4().hex,
                "finding_id": f.id,
                "transaction_ids": transaction_ids,
                "action": "confirm",
                "note": note,
            }
        ],
        expected_current=digest(review.current.to_wire()),
        supersede=True,
    )


def attach_evidence(directory, number, transaction_ids, *, note):
    if not note.strip():
        raise SimplifiError("Evidence requires a nonempty explanation")
    with JournalStore(Path(directory) / "execution").locked(), JournalStore(directory).locked():
        review = load_review(directory)
        f = _selection(review, number, transaction_ids)
        wire = review.to_wire()
        wire["history"].append(
            {
                "kind": "evidence",
                "id": uuid4().hex,
                "finding_id": f.id,
                "transaction_ids": transaction_ids,
                "recorded_at": now(),
                "note": note,
                "transactions": [
                    t
                    for t in review.current.transactions
                    if str(t["id"]) in {review.replacements.get(i, i) for i in transaction_ids}
                ],
            }
        )
        replace_encrypted(Path(directory) / "review.dpapi", Review.model_validate(wire).to_wire())
    return review_summary(load_review(directory))


def refresh_transactions(client, directory, transaction_ids):
    """Refresh only selected records; invalidate dispositions when evidence changes."""
    with JournalStore(Path(directory) / "execution").locked(), JournalStore(directory).locked():
        review = load_review(directory)
        if review.current.dataset_id != client.dataset_id:
            raise SimplifiError("Dataset differs from review")
        cached = {str(t["id"]): t for t in review.current.transactions}
        if not transaction_ids or len(transaction_ids) != len(set(transaction_ids)):
            raise SimplifiError("Select nonempty unique transaction IDs")
        mapped = {review.replacements.get(i, i) for i in transaction_ids}
        if not mapped <= cached.keys():
            raise SimplifiError("Unknown transaction selection")
        results = {tid: client.transaction(tid) for tid in sorted(mapped)}
        if any(str(t.get("id")) != tid for tid, t in results.items()):
            raise SimplifiError("Refresh returned a different transaction ID")
        wire, changed = refreshed_wire(review, results)
        replace_encrypted(Path(directory) / "review.dpapi", Review.model_validate(wire).to_wire())
    return {
        "refreshed": len(results),
        "changed": len(changed),
        "summary": review_summary(load_review(directory)),
    }


def refreshed_wire(review, results, *, invalidated=()):
    """Pure projection, shared by selected refresh and incremental review sync."""
    cached = {str(t["id"]): t for t in review.current.transactions}
    changed = {tid for tid, t in results.items() if tid in cached and t != cached[tid]}
    affected = set(changed) | set(invalidated)
    changed_evidence = set(affected)
    affected.update(
        o["credit_id"]
        for o in review.context.get("reimbursement_offsets", [])
        if o["expense_id"] in changed_evidence
    )
    for d in review.decisions:
        affected.update(
            review.replacements.get(credit, credit)
            for credit, expense in d.offsets.items()
            if review.replacements.get(expense, expense) in changed_evidence
        )
    wire = review.to_wire()
    wire["current"]["transactions"] = [
        results.get(str(t["id"]), t) for t in review.current.transactions
    ]
    for f in review.findings:
        selected = [
            tid for tid in f.transaction_ids if review.replacements.get(tid, tid) in affected
        ]
        if selected:
            # Revise existing decisions too, invalidating prepared plans.
            for d in wire["decisions"]:
                if d["finding_id"] == f.id and any(t in selected for t in d["transaction_ids"]):
                    d["transaction_ids"] = [t for t in d["transaction_ids"] if t not in selected]
                    for key in ("exceptions", "offsets"):
                        d[key] = {
                            k: v for k, v in d.get(key, {}).items() if k in d["transaction_ids"]
                        }
            wire["decisions"].append(
                {
                    "id": uuid4().hex,
                    "finding_id": f.id,
                    "transaction_ids": selected,
                    "action": "unresolved",
                    "note": "Live evidence changed during refresh; review again.",
                }
            )
    wire["decisions"] = [d for d in wire["decisions"] if d["transaction_ids"]]
    offsets = [
        o
        for o in wire["context"].get("reimbursement_offsets", [])
        if o["credit_id"] not in affected and o["expense_id"] not in affected
    ]
    wire["context"]["reimbursement_offsets"] = offsets
    wire["context"]["used_reimbursement_expense_ids"] = sorted({o["expense_id"] for o in offsets})
    wire["context"]["last_refreshed_at"] = now()
    wire["history"].append(
        {
            "kind": "refresh",
            "recorded_at": now(),
            "results": results,
            "invalidated": sorted(affected),
        }
    )
    return wire, changed
