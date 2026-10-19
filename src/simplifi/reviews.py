"""Encrypted review storage, deterministic analysis, and explicit local rendering."""

from __future__ import annotations

import html
import os
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from simplifi.errors import SimplifiError
from simplifi.journal_store import JournalStore
from simplifi.review_models import Decision, Finding, Review, Snapshot
from simplifi.transaction_rules import digest, load_encrypted, save_encrypted
from simplifi.views.classify import transaction_date, transaction_payee


def now():
    return datetime.now(timezone.utc).isoformat()


def load_review(directory):
    try:
        review = Review.model_validate(load_encrypted(Path(directory) / "review.dpapi"))
        if load_encrypted(Path(directory) / "original.dpapi") != review.original.to_wire():
            raise SimplifiError("Immutable original differs from review")
        return review
    except (ValueError, KeyError, TypeError):
        raise SimplifiError("Invalid encrypted review; details suppressed") from None


def replace_encrypted(path, value):
    """Write a complete encrypted file before replacing the previous projection."""
    path = Path(path)
    temporary = path.with_name(f".{uuid4().hex}.dpapi")
    try:
        save_encrypted(temporary, value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def create_review(directory, review):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # The aggregate includes its immutable original. Back it up independently too.
    with JournalStore(directory).locked():
        if (directory / "review.dpapi").exists():
            raise SimplifiError("Review destination already contains a review")
        original_path = directory / "original.dpapi"
        if original_path.exists():
            if load_encrypted(original_path) != review.original.to_wire():
                raise SimplifiError("Existing immutable original differs from new review")
        else:
            save_encrypted(original_path, review.original.to_wire())
        save_encrypted(directory / "review.dpapi", review.to_wire())


def category_name(transaction, categories):
    coa = transaction.get("coa") or {}
    return categories.get(str(coa.get("id")), coa.get("type", "Unknown"))


def resolve_category(categories, *, category_id=None, name=None):
    matches = [
        c
        for c in categories
        if not c.get("isDeleted")
        and not c.get("isNotUserAssignable")
        and (str(c.get("id")) == category_id if category_id is not None else c.get("name") == name)
    ]
    if len(matches) != 1:
        raise SimplifiError("Select exactly one assignable category by ID or unambiguous name")
    return matches[0]


def supersede_decisions(existing, incoming):
    """Replace dispositions for selected entries, retaining unrelated group members."""
    selections = defaultdict(set)
    for d in incoming:
        selections[d.finding_id].update(d.transaction_ids)
    retained = []
    incoming_ids = {d.id for d in incoming}
    for d in existing:
        if d.id in incoming_ids:
            continue
        remaining = [tid for tid in d.transaction_ids if tid not in selections[d.finding_id]]
        if remaining:
            wire = d.to_wire()
            wire["transaction_ids"] = remaining
            for field in ("exceptions", "offsets"):
                wire[field] = {k: v for k, v in wire[field].items() if k in remaining}
            retained.append(Decision.model_validate(wire))
    return retained + incoming


def record_decisions(directory, decisions, *, expected_current=None, supersede=False):
    """Upsert explicit decision IDs; changes do not authenticate or execute writes."""
    parsed = [Decision.model_validate(d) for d in decisions]
    if not parsed or len({d.id for d in parsed}) != len(parsed):
        raise SimplifiError("Provide nonempty decisions with unique IDs")
    with JournalStore(Path(directory) / "execution").locked(), JournalStore(directory).locked():
        review = load_review(directory)
        if expected_current is not None and digest(review.current.to_wire()) != expected_current:
            raise SimplifiError("Review evidence changed before recording the decision")
        existing = supersede_decisions(review.decisions, parsed) if supersede else review.decisions
        updates = {d.id: d for d in existing}
        updates.update({d.id: d for d in parsed})
        wire = review.to_wire()
        wire["decisions"] = [d.to_wire() for d in updates.values()]
        txns = {str(t["id"]): t for t in review.current.transactions}
        revised_credits = {
            review.replacements.get(tid, tid)
            for d in parsed
            if d.action in {"category", "unresolved"}
            for tid in d.transaction_ids
        }
        offsets = [
            o
            for o in review.context.get("reimbursement_offsets", [])
            if o["credit_id"] not in revised_credits
        ]
        used = {o["expense_id"]: o["credit_id"] for o in offsets}
        for d in updates.values():
            for credit, expense in d.offsets.items():
                credit = review.replacements.get(credit, credit)
                if credit not in txns or expense not in txns:
                    raise SimplifiError("Offset requires existing credit and expense evidence")
                if (
                    not ordinary(txns[credit])
                    or not ordinary(txns[expense])
                    or Decimal(str(txns[credit]["amount"])) <= 0
                    or Decimal(str(txns[expense]["amount"])) >= 0
                ):
                    raise SimplifiError("Offset requires an ordinary credit and expense")
                if expense in used and used[expense] != credit:
                    raise SimplifiError("Expense already assigned to another reimbursement")
                used[expense] = credit
                evidence = {
                    "credit_id": credit,
                    "expense_id": expense,
                    "amount_difference": str(
                        Decimal(str(txns[credit]["amount"])) + Decimal(str(txns[expense]["amount"]))
                    ),
                }
                if evidence not in offsets:
                    offsets.append(evidence)
        wire["context"]["used_reimbursement_expense_ids"] = sorted(used)
        wire["context"]["reimbursement_offsets"] = offsets
        wire["history"].append(
            {"kind": "decisions", "recorded_at": now(), "decisions": [d.to_wire() for d in parsed]}
        )
        validated = Review.model_validate(wire)
        replace_encrypted(Path(directory) / "review.dpapi", validated.to_wire())
    return review_summary(validated)


def ordinary(t):
    return (
        t.get("type") == "CASH_FLOW"
        and not t.get("isDeleted")
        and not t.get("transfer")
        and not t.get("split")
        and (t.get("coa") or {}).get("type") in {"CATEGORY", "UNCATEGORIZED"}
    )


def reimbursement_candidates(
    snapshot, credit_id, *, days_before=45, days_after=3, used_expense_ids=()
):
    if days_before < 0 or days_after < 0:
        raise SimplifiError("Date windows must be nonnegative")
    credit = next((t for t in snapshot.transactions if str(t["id"]) == credit_id), None)
    if credit is None or not ordinary(credit) or Decimal(str(credit["amount"])) <= 0:
        raise SimplifiError("Select an ordinary incoming transaction")
    when = date.fromisoformat(transaction_date(credit))
    amount = Decimal(str(credit["amount"]))
    result = []
    for expense in snapshot.transactions:
        if not ordinary(expense) or str(expense["id"]) in used_expense_ids:
            continue
        delta = (when - date.fromisoformat(transaction_date(expense))).days
        if Decimal(str(expense["amount"])) == -amount and -days_after <= delta <= days_before:
            result.append(
                {
                    "transaction_id": str(expense["id"]),
                    "days_before": delta,
                    "category": expense.get("coa"),
                    "amount_difference": "0.00",
                }
            )
    return {
        "credit_id": credit_id,
        "candidates": result,
        "ambiguous": len(result) > 1,
        "automatic_action": False,
    }


def analyze(snapshot, *, existing=(), preferences=None, resolved_ids=()):
    """Candidates only, retaining prior IDs, numbers, evidence, and group members."""
    from simplifi.reconciliation import investment_activity
    from simplifi.review_preferences import ReviewPreferences, normalize

    preferences = preferences or ReviewPreferences()
    accounts = {str(a["id"]): a for a in snapshot.accounts}
    groups = defaultdict(list)
    for t in snapshot.transactions:
        day = transaction_date(t)
        if (
            ordinary(t)
            and not investment_activity(t, accounts)
            and day
            and snapshot.start.isoformat() <= day <= snapshot.end.isoformat()
        ):
            owner = preferences.account_owners.get(str(t.get("accountId")))
            groups[(owner or "", preferences.payee(t))].append(t)
    findings = list(existing)
    indexes = {f.id: i for i, f in enumerate(findings)}
    number = max((f.number for f in findings), default=0)
    aliases = {
        normalize(k): [normalize(v) for v in values]
        for k, values in preferences.statement_aliases.items()
    }
    for (owner, payee), rows in sorted(groups.items()):
        candidates = []
        accepted = {
            str(t["id"]) for t in rows if any(c.accepts(t) for c in preferences.conventions)
        }
        if len({digest(t.get("coa")) for t in rows}) > 1:
            candidates.append(
                (
                    "mixed",
                    [t for t in rows if str(t["id"]) not in accepted],
                    "Same payee has different categories",
                    "Different purchases or reimbursements may justify this difference.",
                )
            )
        outside_convention = [
            t
            for t in rows
            if any(c.matches_scope(t) for c in preferences.conventions)
            and str(t["id"]) not in accepted
        ]
        if outside_convention:
            candidates.append(
                (
                    "convention",
                    outside_convention,
                    "Assignment differs from a recorded analysis convention",
                    "Review the explicit category and exclusion preferences; no change is automatic.",
                )
            )
        mismatched = []
        for t in rows:
            statement = normalize((t.get("cpData") or {}).get("payee", ""))
            actual_payee = normalize(transaction_payee(t))
            if (
                statement
                and actual_payee not in statement
                and payee not in statement
                and not any(alias in statement for alias in aliases.get(payee, []))
            ):
                mismatched.append(t)
        if mismatched:
            candidates.append(
                (
                    "descriptor",
                    mismatched,
                    "Payee differs from bank description",
                    "Merchant normalization may be legitimate; inspect the description.",
                )
            )
        for kind, selected, title, rationale in candidates:
            fid = digest([snapshot.dataset_id, kind, payee] + ([owner] if owner else []))
            ids = [str(t["id"]) for t in selected if str(t["id"]) not in resolved_ids]
            if not ids:
                continue
            if fid in indexes:
                previous = findings[indexes[fid]]
                wire = previous.to_wire()
                wire["transaction_ids"] = list(dict.fromkeys(previous.transaction_ids + ids))
                findings[indexes[fid]] = Finding.model_validate(wire)
                continue
            number += 1
            finding = Finding(
                id=fid,
                number=number,
                title=title,
                rationale=rationale,
                transaction_ids=ids,
                evidence={
                    "analysis": kind,
                    "payee": payee,
                    "owner": owner or None,
                    "reference_transaction_ids": [str(t["id"]) for t in rows],
                },
            )
            indexes[fid] = len(findings)
            findings.append(finding)
    return findings


def download_review(client, directory, *, start, end):
    if not client.dataset_id:
        raise SimplifiError("Resolve a dataset before downloading")
    if start > end:
        raise SimplifiError("Invalid date range")
    transactions = [
        t
        for t in client.transactions(date_on_after=start.isoformat())
        if start.isoformat() <= transaction_date(t) <= end.isoformat()
    ]
    snapshot = Snapshot(
        dataset_id=client.dataset_id,
        downloaded_at=now(),
        start=start,
        end=end,
        transactions=transactions,
        categories=client.categories(),
        accounts=client.accounts(),
    )
    review = Review(id=uuid4().hex, original=snapshot, current=snapshot, findings=analyze(snapshot))
    create_review(directory, review)
    return review_summary(review)


def execution_outcomes(review):
    latest = {}
    for entry in review.history:
        if entry.get("kind") == "execution":
            latest.update(
                {
                    review.replacements.get(tid, tid): outcome
                    for tid, outcome in entry.get("outcomes", {}).items()
                }
            )
        elif entry.get("kind") == "refresh":
            for tid in entry.get("invalidated", []):
                latest.pop(review.replacements.get(tid, tid), None)
    return latest


def review_summary(review):
    # Last decision for a finding/transaction is its current disposition.
    dispositions = {}
    for d in review.decisions:
        for tid in d.transaction_ids:
            dispositions[(d.finding_id, tid)] = d.action
    counts = Counter(dispositions.values())
    unresolved = sum(
        dispositions.get((f.id, tid), "unresolved") == "unresolved"
        for f in review.findings
        for tid in f.transaction_ids
    )
    outcomes = Counter(execution_outcomes(review).values())
    return {
        "findings": len(review.findings),
        "transactions": len(review.current.transactions),
        "changed": outcomes["changed"],
        "already_correct": outcomes["already_correct"],
        "confirmed": counts["confirm"],
        "dismissed": counts["dismiss"],
        "unresolved": unresolved,
        "blocked": len(review.context.get("import_issues", []))
        + len(review.context.get("sync_issues", [])),
    }


def render_review(directory, output):
    if Path(output).suffix.lower() != ".html":
        raise SimplifiError("Render output must be an HTML file")
    review = load_review(directory)
    categories = {str(c["id"]): c["name"] for c in review.current.categories}
    original_categories = {
        str(c["id"]): c["name"] for c in review.context.get("added_categories", {}).values()
    }
    original_categories.update({str(c["id"]): c["name"] for c in review.original.categories})
    original = {
        **review.context.get("added_originals", {}),
        **{str(t["id"]): t for t in review.original.transactions},
    }
    current = {str(t["id"]): t for t in review.current.transactions}
    decisions = {(d.finding_id, tid): d for d in review.decisions for tid in d.transaction_ids}
    outcomes = execution_outcomes(review)
    esc = lambda value: html.escape(str(value), quote=True)
    parts = [
        '<!doctype html><html><head><meta charset="utf-8">',
        (
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            "style-src 'unsafe-inline'\">"
        ),
        (
            "<title>Transaction review</title><style>body{font:16px system-ui;max-width:1200px;"
            "margin:2rem auto}table{border-collapse:collapse}td,th{padding:.5rem;"
            "border:1px solid #ccc}article{margin:2rem 0}</style></head><body>"
        ),
        "<h1>Transaction review</h1><p>Private local financial data. Snapshot: "
        + esc(review.current.downloaded_at)
        + "</p>",
        "<p>" + esc(review_summary(review)) + "</p>",
    ]
    for f in review.findings:
        parts.append(
            f'<article id="finding-{esc(f.id)}"><h2>#{f.number} · {esc(f.title)}</h2>'
            f"<p>{esc(f.rationale)}</p><p>Original proposal: {esc(f.proposed)}</p>"
            "<table><tr><th>Date</th><th>Payee</th><th>Amount</th><th>Original category</th>"
            "<th>Current category</th><th>Decision / result</th><th>Note</th>"
            "<th>Bank description</th><th>Memo</th><th>Transaction ID</th></tr>"
        )
        for tid in f.transaction_ids:
            mapped = review.replacements.get(tid, tid)
            t = current.get(mapped, original.get(tid, {}))
            d = decisions.get((f.id, tid))
            status = d.action if d else "unresolved"
            if mapped in outcomes:
                status += " / " + outcomes[mapped]
            issues = [
                issue.get("reason", "")
                for issue in review.context.get("import_issues", [])
                + review.context.get("sync_issues", [])
                if issue.get("transaction_id") in {tid, mapped}
                and issue.get("finding", f.number) == f.number
            ]
            note = " ".join(([d.note] if d else []) + issues)
            values = [
                transaction_date(t),
                transaction_payee(t),
                t.get("amount"),
                category_name(original.get(tid, {}), original_categories),
                category_name(t, categories) if mapped in current else "Missing current record",
                status,
                note,
                (t.get("cpData") or {}).get("payee", ""),
                t.get("memo", ""),
                mapped,
            ]
            parts.append("<tr>" + "".join("<td>" + esc(v) + "</td>" for v in values) + "</tr>")
        parts.append("</table></article>")
        for evidence in review.history:
            if evidence.get("kind") == "evidence" and evidence.get("finding_id") == f.id:
                parts.append("<p>Evidence: " + esc(evidence.get("note", "")) + "</p>")
    parts.append("</body></html>")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Explicit render may replace this derived artifact atomically.
    temporary = output.with_name("." + uuid4().hex + ".html")
    try:
        temporary.write_text("".join(parts), encoding="utf-8")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return review_summary(review)
