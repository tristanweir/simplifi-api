"""Transaction classification helpers for spending views."""

from __future__ import annotations

from typing import Any


EXCLUSION_REASONS = {
    "undated": "No usable transaction date.",
    "projected": "Dated after the as-of date (future / projected).",
    "transfer": "Transfer between own accounts.",
    "balance-adjustment": "Balance adjustment / reconciliation entry.",
    "investment": "Investment activity inside an investment account.",
    "income": "Income inflow (excluded from expense totals).",
}


def transaction_date(txn: dict[str, Any]) -> str | None:
    cp = txn.get("cpData") or {}
    for key in ("txnOn", "postedOn", "date", "postedOnDate"):
        value = cp.get(key) or txn.get(key)
        if isinstance(value, str) and value:
            return value[:10]
    return None


def transaction_amount(txn: dict[str, Any]) -> float:
    for key in ("amount", "amountCents"):
        if key in txn and txn[key] is not None:
            value = float(txn[key])
            if key == "amountCents":
                return value / 100.0
            return value
    return 0.0


def transaction_payee(txn: dict[str, Any]) -> str:
    for key in ("payee", "payeeName", "merchantName", "description"):
        value = txn.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    cp = txn.get("cpData") or {}
    for key in ("payee", "inferredPayee"):
        value = cp.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "(unknown)"


def category_id(txn: dict[str, Any]) -> str | None:
    coa = txn.get("coa") or {}
    if isinstance(coa, dict):
        if coa.get("type") == "CATEGORY" and coa.get("id"):
            return str(coa["id"])
        if coa.get("id") and not coa.get("type"):
            return str(coa["id"])
    if txn.get("categoryId"):
        return str(txn["categoryId"])
    return None


def classify_for_spending(
    txn: dict[str, Any],
    *,
    as_of: str,
    known_ids: set[str] | None = None,
) -> str:
    """Return a bucket name: spending | income | exclusion kinds."""
    date = transaction_date(txn)
    if not date:
        return "undated"
    if date > as_of:
        return "projected"

    coa = txn.get("coa") or {}
    if isinstance(coa, dict) and str(coa.get("type", "")).upper() == "BALANCE_ADJUSTMENT":
        return "balance-adjustment"

    txn_type = str(txn.get("type") or "").upper()
    if txn_type == "INVESTMENT":
        return "investment"

    # Transfer heuristics used by Simplifi payloads.
    if txn.get("isTransfer") or txn.get("transfer") or txn_type == "TRANSFER":
        return "transfer"
    if isinstance(coa, dict) and str(coa.get("type", "")).upper() == "TRANSFER":
        return "transfer"
    transfer_id = txn.get("transferId") or txn.get("transferTransactionId")
    if transfer_id and known_ids and str(transfer_id) in known_ids:
        return "transfer"

    amount = transaction_amount(txn)
    # Simplifi expenses are typically negative; income positive.
    if amount > 0:
        return "income"
    return "spending"
