"""Scoped category/transfer classification edits; the ordinary client stays read-only."""

from __future__ import annotations

import copy
import json
import math
import os
import re
from pathlib import Path

import requests

from simplifi.constants import APP_BUILD, APP_RELEASE, BASE_URL, CLIENT_ID
from simplifi.errors import ReadOnlyViolation, SimplifiError, SimplifiHTTPError
from simplifi.secure_session import SecureSession
from simplifi.token_store import _crypt

# Matches the production web app's CASH_FLOW transaction request transform.
_FIELDS = frozenset(
    [
        "id",
        "accountId",
        "businessId",
        "customerId",
        "projectId",
        "clientId",
        "postedOn",
        "payee",
        "memo",
        "knownCategoryId",
        "coa",
        "amount",
        "tags",
        "attachments",
        "split",
        "state",
        "matchState",
        "isReviewed",
        "cpData",
        "source",
        "stModelId",
        "stDueOn",
        "paymentProvider",
        "isExcludedFromF2S",
        "isExcludedFromReports",
        "userFlag",
        "userFlagNote",
        "isDeleted",
        "transfer",
        "isBill",
        "isSubscription",
        "isBillable",
        "subtype",
        "subtypeRelatedTxn",
        "linkedItem",
        "allocations",
        "logoId",
        "logoUrl",
        "check",
    ]
)


def category_payload(original, category):
    if (
        original.get("type") != "CASH_FLOW"
        or original.get("split")
        or original.get("transfer")
        or original.get("isDeleted")
        or not (
            original.get("coa", {}).get("type") == "CATEGORY"
            or original.get("coa") == {"type": "UNCATEGORIZED", "id": "0"}
        )
    ):
        raise ReadOnlyViolation("Only ordinary, unsplit category transactions are supported")
    if category.get("isDeleted") or category.get("isNotUserAssignable") or not category.get("id"):
        raise ReadOnlyViolation("Target category is not assignable")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(original.get("id", ""))):
        raise ReadOnlyViolation("Invalid transaction identifier")
    payload = {key: copy.deepcopy(value) for key, value in original.items() if key in _FIELDS}
    payload["type"] = "CASH_FLOW"
    payload["coa"] = {"type": "CATEGORY", "id": str(category["id"])}
    return payload


class _SinglePayloadSession(SecureSession):
    def __init__(self, original, payload):
        super().__init__()
        self._url = f"{BASE_URL}/transactions/{original['id']}"
        self._payload = copy.deepcopy(payload)
        self._used = False

    def request(self, method, url, **kwargs):
        if method.upper() != "PUT":
            return super().request(method, url, **kwargs)
        if (
            self._used
            or url != self._url
            or kwargs.get("json") != self._payload
            or kwargs.get("params")
            or kwargs.get("data")
        ):
            raise ReadOnlyViolation("Write does not match the single selected category change")
        self._used = True
        kwargs.update(allow_redirects=False, verify=True, timeout=30)
        try:
            return requests.Session.request(self, method, url, **kwargs)
        except requests.RequestException:
            raise SimplifiError(
                "Category update outcome uncertain; read transaction before retrying"
            ) from None


class SingleCategorySession(_SinglePayloadSession):
    def __init__(self, original, category):
        super().__init__(original, category_payload(original, category))


def transfer_classification_payload(original, *, incoming):
    """Classify an existing cash-flow record; never creates a linked counterpart."""
    if (
        type(incoming) is not bool
        or original.get("type") != "CASH_FLOW"
        or original.get("split")
        or original.get("transfer")
        or original.get("isDeleted")
        or not re.fullmatch(r"[A-Za-z0-9_-]+", str(original.get("id", "")))
        or not (
            original.get("coa", {}).get("type") == "CATEGORY"
            or original.get("coa") == {"type": "BALANCE_ADJUSTMENT", "id": "2"}
        )
    ):
        raise ReadOnlyViolation("Only unlinked, unsplit cash-flow classifications are supported")
    amount = original.get("amount")
    if type(amount) not in (int, float) or not math.isfinite(amount) or amount == 0:
        raise ReadOnlyViolation("An existing finite nonzero amount is required")
    payload = {key: copy.deepcopy(value) for key, value in original.items() if key in _FIELDS}
    payload.update(
        type="CASH_FLOW",
        coa={"type": "BALANCE_ADJUSTMENT", "id": "2"},
        amount=abs(amount) if incoming else -abs(amount),
    )
    return payload


def update_transfer_classification(client, original, *, incoming, backup_path: Path):
    """Set generic Transfer and explicit direction without moving money or linking records."""
    current = client.transaction(original["id"])
    if current != original:
        raise SimplifiError("Transaction changed since selection; read it again before updating")
    payload = transfer_classification_payload(current, incoming=incoming)
    return _execute_correction(
        client,
        current,
        payload,
        backup_path=backup_path,
        backup={
            "dataset_id": client.dataset_id,
            "original": current,
            "target_coa": payload["coa"],
            "incoming": incoming,
        },
        session=_SinglePayloadSession(current, payload),
        allowed_changes={"coa", "amount"},
    )


def update_category(client, original, category, *, backup_path: Path):
    current = client.transaction(original["id"])
    if current != original:
        raise SimplifiError("Transaction changed since selection; read it again before updating")
    payload = category_payload(current, category)
    return _execute_correction(
        client,
        current,
        payload,
        backup_path=backup_path,
        backup={"dataset_id": client.dataset_id, "original": current, "target_category": category},
        session=SingleCategorySession(current, category),
        allowed_changes={"coa"},
    )


def _execute_correction(client, current, payload, *, backup_path, backup, session, allowed_changes):
    if all(current.get(key) == payload.get(key) for key in allowed_changes):
        session.close()
        return current
    if backup_path.suffix != ".dpapi":
        session.close()
        raise ReadOnlyViolation("Encrypted backup requires a .dpapi filename")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    # Create exclusively; never overwrite the original backup during a retry.
    encrypted = _crypt(json.dumps(backup).encode())
    with backup_path.open("xb") as stream:
        stream.write(encrypted)
        stream.flush()
        os.fsync(stream.fileno())
    headers = {
        "Authorization": f"Bearer {client.auth.ensure_access_token()}",
        "Qcs-Dataset-Id": client.dataset_id,
        "app-client-id": CLIENT_ID,
        "app-release": APP_RELEASE,
        "app-build": APP_BUILD,
        "accept": "application/json",
    }
    with session:
        response = session.request("PUT", session._url, json=payload, headers=headers)
    # Never automatically replay a write, including after 401 or a timeout.
    if not 200 <= response.status_code < 300:
        raise SimplifiHTTPError(response.status_code, "category update", "Response suppressed")
    verified = client.transaction(current["id"])
    verify_correction(current, payload, verified, allowed_changes=allowed_changes)
    return verified


def verify_correction(current, payload, verified, *, allowed_changes):
    """Validate a read-back, including reconciliation after uncertain dispatch."""
    if any(verified.get(key) != payload.get(key) for key in allowed_changes):
        raise SimplifiError("Category update was sent but the read-back category did not match")
    changes = {key for key in set(current) | set(verified) if current.get(key) != verified.get(key)}
    unexpected = changes - (allowed_changes | {"modifiedAt", "dbVersion", "userModifiedAt"})
    # Observed server normalization after a user edits a bank-pending transaction.
    if (current.get("source"), verified.get("source")) == (
        "QCS_REFRESH_BANK_PENDING",
        "USER_OWNED_BANK_PENDING",
    ):
        unexpected.discard("source")
        cp_before = copy.deepcopy(current.get("cpData") or {})
        cp_before.pop("id", None)
        if verified.get("cpData") == cp_before:
            unexpected.discard("cpData")
    if unexpected:
        raise SimplifiError(
            "Category updated, but other fields changed during verification; inspect before further edits"
        )
