"""Read-only sync and offline reanalysis of existing encrypted reviews."""

from datetime import date, datetime
from decimal import InvalidOperation
from pathlib import Path

from simplifi.errors import SimplifiError, SimplifiHTTPError
from simplifi.journal_store import JournalStore
from simplifi.reconciliation import amount
from simplifi.review_management import refreshed_wire
from simplifi.review_models import Review
from simplifi.review_preferences import ReviewPreferences, preferences_for
from simplifi.reviews import (
    analyze,
    execution_outcomes,
    load_review,
    now,
    replace_encrypted,
    review_summary,
)
from simplifi.views.classify import transaction_date


def _validate_transaction(transaction):
    if transaction.get("isDeleted") is not None and type(transaction["isDeleted"]) is not bool:
        raise SimplifiError("Unknown deletion state in sync")
    if not transaction.get("isDeleted"):
        try:
            date.fromisoformat(transaction_date(transaction))
            amount(transaction)
        except (TypeError, ValueError, InvalidOperation):
            raise SimplifiError("Sync contains an invalid transaction date or amount") from None


def _analyzed(review):
    preferences = preferences_for(review)
    dispositions = {
        (d.finding_id, review.replacements.get(tid, tid)): d.action
        for d in review.decisions
        for tid in d.transaction_ids
    }
    resolved = set(execution_outcomes(review))
    resolved.update(
        tid for (_, tid), action in dispositions.items() if action in {"confirm", "dismiss"}
    )
    resolved.difference_update(
        tid for (_, tid), action in dispositions.items() if action == "unresolved"
    )
    findings = analyze(
        review.current, existing=review.findings, preferences=preferences, resolved_ids=resolved
    )
    wire = review.to_wire()
    wire["findings"] = [f.to_wire() for f in findings]
    wire["context"]["analysis_at"] = now()
    wire["history"].append(
        {"kind": "analysis", "recorded_at": now(), "finding_count": len(findings)}
    )
    return Review.model_validate(wire)


def reanalyze_review(directory):
    with JournalStore(Path(directory) / "execution").locked(), JournalStore(directory).locked():
        review = _analyzed(load_review(directory))
        replace_encrypted(Path(directory) / "review.dpapi", review.to_wire())
    return review_summary(review)


def set_review_preferences(directory, value):
    preferences = ReviewPreferences.model_validate(value)
    with JournalStore(Path(directory) / "execution").locked(), JournalStore(directory).locked():
        review = load_review(directory)
        accounts = {str(a["id"]) for a in review.current.accounts}
        accounts.update(
            str(t["accountId"]) for t in review.current.transactions if t.get("accountId")
        )
        categories = {str(c["id"]) for c in review.current.categories}
        selected = set(preferences.account_owners)
        for convention in preferences.conventions:
            selected.update(convention.account_ids)
            if not set(convention.category_ids) <= categories:
                raise SimplifiError("Convention references an unknown category")
        for channel in preferences.payment_channels:
            selected.update(channel.bank_account_ids)
            selected.add(channel.payment_account_id)
        if not selected <= accounts:
            raise SimplifiError("Analysis preferences reference unknown accounts")
        wire = review.to_wire()
        wire["context"]["analysis_preferences"] = preferences.to_wire()
        wire["history"].append(
            {
                "kind": "analysis_preferences",
                "recorded_at": now(),
                "preferences": preferences.to_wire(),
            }
        )
        replace_encrypted(Path(directory) / "review.dpapi", Review.model_validate(wire).to_wire())
    return {"saved": True, "live_changes": False, "reanalyze_required": True}


def sync_review(client, directory, *, modified_after=None):
    if modified_after is not None:
        try:
            stamp = datetime.fromisoformat(modified_after.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("Timezone required")
        except (TypeError, ValueError):
            raise SimplifiError("Modified-after must be an ISO timestamp with timezone") from None
    with JournalStore(Path(directory) / "execution").locked(), JournalStore(directory).locked():
        review = load_review(directory)
        if client.dataset_id != review.current.dataset_id:
            raise SimplifiError("Dataset differs from review")
        started = now()
        cached = {str(t["id"]): t for t in review.current.transactions}
        # A delta is unbounded by date so moved records and tombstones can be observed.
        rows = (
            client.transactions(modified_after=modified_after)
            if modified_after
            else client.transactions(date_on_after=review.current.start.isoformat())
        )
        observed = {str(t["id"]): t for t in rows}
        if len(observed) != len(rows):
            raise SimplifiError("Duplicate transaction IDs in sync")
        results, added = {}, {}
        for tid, t in observed.items():
            _validate_transaction(t)
            if tid in review.replacements and not t.get("isDeleted"):
                raise SimplifiError(
                    "A previously replaced ID reappeared; investigate before syncing"
                )
            day = transaction_date(t)
            inside = (
                day and review.current.start.isoformat() <= day <= review.current.end.isoformat()
            )
            if tid in cached:
                results[tid] = t
            elif inside and not t.get("isDeleted"):
                added[tid] = t
        issues = {i["transaction_id"]: i for i in review.context.get("sync_issues", [])}
        for tid in observed:
            issues.pop(tid, None)
        if modified_after is None:
            # An absent list row is not proof of deletion or a replacement ID.
            for tid in sorted(cached.keys() - observed.keys()):
                try:
                    t = client.transaction(tid)
                    if str(t.get("id")) != tid:
                        raise SimplifiError("Sync lookup returned a different ID")
                    _validate_transaction(t)
                    results[tid] = t
                    issues.pop(tid, None)
                except SimplifiHTTPError as error:
                    if error.status_code not in {400, 404}:
                        raise
                    issues[tid] = {
                        "transaction_id": tid,
                        "reason": "Record missing from snapshot and exact lookup; investigate without guessing replacement",
                    }
        wire, changed = refreshed_wire(review, results, invalidated=issues.keys())
        wire["current"]["transactions"].extend(added.values())
        wire["current"]["categories"] = client.categories()
        wire["current"]["accounts"] = client.accounts()
        wire["current"]["downloaded_at"] = started
        first_seen = wire["context"].setdefault("added_originals", {})
        for tid, t in added.items():
            if tid not in {str(t["id"]) for t in review.original.transactions}:
                first_seen.setdefault(tid, t)
        original_categories = {str(c["id"]) for c in review.original.categories}
        for c in wire["current"]["categories"]:
            if str(c["id"]) not in original_categories:
                wire["context"].setdefault("added_categories", {}).setdefault(str(c["id"]), c)
        wire["context"]["sync_issues"] = list(issues.values())
        wire["history"].append(
            {
                "kind": "sync",
                "started_at": started,
                "modified_after": modified_after,
                "added_ids": sorted(added),
                "changed_ids": sorted(changed),
                "issues": list(issues.values()),
            }
        )
        updated = _analyzed(Review.model_validate(wire))
        replace_encrypted(Path(directory) / "review.dpapi", updated.to_wire())
    return {
        "added": len(added),
        "changed": len(changed),
        "missing": len(issues),
        "delta": modified_after is not None,
        "summary": review_summary(updated),
        "live_changes": False,
        "coverage_warning": "Delta absence cannot establish deletion; periodically run a full sync."
        if modified_after
        else None,
    }
