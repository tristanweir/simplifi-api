"""Read-only exact-amount evidence for account trails and bank-funded payments."""

from datetime import date
from decimal import Decimal, InvalidOperation

from simplifi.errors import SimplifiError
from simplifi.review_preferences import normalize, preferences_for
from simplifi.views.classify import transaction_date


def amount(transaction):
    value = transaction.get("amount")
    if isinstance(value, bool) or value is None:
        raise ValueError("Amount missing or invalid")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Amount must be finite")
    return result


def investment_activity(transaction, accounts):
    cp = transaction.get("cpData") or {}
    action = transaction.get("investmentTxnType") or cp.get("investmentTxnType")
    account = accounts.get(str(transaction.get("accountId")), {})
    return bool(
        transaction.get("type") == "INVESTMENT"
        or action
        or any(
            record.get(key) is not None
            for record in (transaction, cp)
            for key in ("units", "securityId", "localSecurityId")
        )
        or (
            account.get("type") == "INVESTMENT"
            and cp.get("txnType") in {"TRANSFER", "JRNLFUND", "XFER", "CONTRIBUTION", "OTHER"}
        )
    )


def reconcile_transaction(review, transaction_id, *, days=7, max_hops=4, max_paths=50):
    if type(days) is not int or not 0 <= days <= 366:
        raise SimplifiError("Date window must be between 0 and 366 days")
    if (
        type(max_hops) is not int
        or type(max_paths) is not int
        or not 1 <= max_hops <= 10
        or not 1 <= max_paths <= 1000
    ):
        raise SimplifiError("Invalid trail limits")
    preferences = preferences_for(review)
    accounts = {str(a["id"]): a for a in review.current.accounts}
    rows = {str(t["id"]): t for t in review.current.transactions if not t.get("isDeleted")}
    tid = review.replacements.get(transaction_id, transaction_id)
    if tid not in rows:
        raise SimplifiError("Select an existing review transaction")
    anchor = rows[tid]
    try:
        magnitude = abs(amount(anchor))
        when = date.fromisoformat(transaction_date(anchor))
    except (ValueError, TypeError, InvalidOperation):
        raise SimplifiError("Selected transaction needs a finite amount and valid date") from None
    selected, dates, amounts = {tid: anchor}, {tid: when}, {tid: amount(anchor)}
    warnings = (
        [{"transaction_id": tid, "reason": "A zero amount cannot establish a cash transfer."}]
        if magnitude == 0
        else []
    )
    for key, row in rows.items() if magnitude else ():
        try:
            value = amount(row)
            day = date.fromisoformat(transaction_date(row))
        except (ValueError, TypeError, InvalidOperation):
            continue
        if abs(value) == magnitude and abs((day - when).days) <= days:
            selected[key], dates[key], amounts[key] = row, day, value
    edges = []
    ordered = sorted(selected.items())
    for aid, a in ordered:
        for bid, b in ordered:
            if aid == bid:
                continue
            if not a.get("accountId") or not b.get("accountId"):
                continue
            delta = (dates[bid] - dates[aid]).days
            same_account = a.get("accountId") == b.get("accountId")
            ac = accounts.get(str(a.get("accountId")), {})
            bc = accounts.get(str(b.get("accountId")), {})
            ca, cb = (
                ac.get("currency") or ac.get("currencyCode"),
                bc.get("currency") or bc.get("currencyCode"),
            )
            if ca and cb and ca != cb:
                continue
            currency = "verified_equal" if ca and cb else "unverified"
            reciprocal = (
                not same_account
                and amounts[aid] < 0 < amounts[bid]
                and (a.get("transfer") or {}).get("id") == bid
                and (b.get("transfer") or {}).get("id") == aid
                and a.get("coa") == {"type": "ACCOUNT", "id": b.get("accountId")}
                and b.get("coa") == {"type": "ACCOUNT", "id": a.get("accountId")}
            )
            kind, direction = None, None
            if amounts[aid] < 0 < amounts[bid] and not same_account:
                kind = "linked_transfer" if reciprocal else "possible_transfer"
                direction = (aid, bid)
            elif amounts[aid] > 0 > amounts[bid] and same_account and delta >= 0:
                kind = (
                    "investment_cash_flow"
                    if (investment_activity(a, accounts) or investment_activity(b, accounts))
                    else "same_account_cash_flow"
                )
                direction = (aid, bid)
            elif amounts[aid] == amounts[bid] and amounts[aid] < 0 and not same_account:
                statement = normalize(
                    " ".join(str((a.get("cpData") or {}).get(k) or "") for k in ("payee", "memo"))
                )
                if any(
                    str(a.get("accountId")) in c.bank_account_ids
                    and str(b.get("accountId")) == c.payment_account_id
                    and normalize(c.statement_contains) in statement
                    for c in preferences.payment_channels
                ):
                    kind, direction = "bank_funded_payment", (aid, bid)
            if kind:
                edges.append(
                    {
                        "from": direction[0],
                        "to": direction[1],
                        "kind": kind,
                        "amount": str(magnitude),
                        "days": delta,
                        "currency": currency,
                        "verified_link": reciprocal,
                        "amount_difference": "0",
                        "evidence": "Reciprocal account and transaction references"
                        if reciprocal
                        else "Equal decimal amounts and nearby dates; account/source evidence still requires review",
                    }
                )
    # Preserve every direct candidate; limit only graph traversal, never silently pick a match.
    trails, truncated, depth_limited = [], False, False
    adjacency = {}
    for edge in edges:
        adjacency.setdefault(edge["from"], []).append(edge["to"])
        adjacency.setdefault(edge["to"], []).append(edge["from"])
    connected, pending = {tid}, [tid]
    while pending:
        for key in adjacency.get(pending.pop(), []):
            if key not in connected:
                connected.add(key)
                pending.append(key)
    selected = {key: row for key, row in selected.items() if key in connected}
    edges = [e for e in edges if e["from"] in connected and e["to"] in connected]

    def walk(path):
        nonlocal truncated, depth_limited
        neighbors = sorted(set(adjacency.get(path[-1], [])) - set(path))
        if len(path) > 1:
            if len(trails) >= max_paths:
                truncated = True
                return
            trails.append(path)
        if len(path) - 1 < max_hops:
            for neighbor in neighbors:
                walk(path + [neighbor])
                if truncated:
                    break
        elif neighbors:
            depth_limited = True

    walk([tid])
    for row in selected.values():
        link = (row.get("transfer") or {}).get("id")
        if link and str(link) not in selected:
            warnings.append(
                {
                    "transaction_id": str(row["id"]),
                    "reason": "Linked counterpart is missing, outside the window, or differs in amount",
                }
            )
        elif link and not any(
            e["verified_link"] and str(row["id"]) in {e["from"], e["to"]} for e in edges
        ):
            warnings.append(
                {
                    "transaction_id": str(row["id"]),
                    "reason": "Transfer references are not reciprocal or accounts/direction disagree",
                }
            )
    return {
        "transaction_id": tid,
        "amount": str(magnitude),
        "days": days,
        "nodes": [
            {
                "transaction": t,
                "account": accounts.get(str(t.get("accountId"))),
                "owner": preferences.account_owners.get(str(t.get("accountId"))),
                "investment_activity": investment_activity(t, accounts),
            }
            for t in selected.values()
        ],
        "edges": edges,
        "trails": trails,
        "trails_truncated": truncated,
        "depth_limited": depth_limited,
        "ambiguous": any(len(set(adjacency.get(key, []))) > 2 for key in connected)
        or len(set(adjacency.get(tid, []))) > 1,
        "warnings": warnings,
        "automatic_action": False,
        "note": "Candidates are evidence, not confirmed offsets, categories, or transfer links.",
    }
