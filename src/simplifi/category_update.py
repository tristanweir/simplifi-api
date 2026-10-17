"""Explicit single-transaction category updates; the ordinary client stays read-only."""

from __future__ import annotations

import copy
import json
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
        or original.get("coa", {}).get("type") != "CATEGORY"
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


class SingleCategorySession(SecureSession):
    def __init__(self, original, category):
        super().__init__()
        self._url = f"{BASE_URL}/transactions/{original['id']}"
        self._payload = category_payload(original, category)
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


def update_category(client, original, category, *, backup_path: Path):
    current = client.transaction(original["id"])
    if current != original:
        raise SimplifiError("Transaction changed since selection; read it again before updating")
    payload = category_payload(current, category)
    if current.get("coa") == payload["coa"]:
        return current
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    # Create exclusively; never overwrite the original backup during a retry.
    encrypted = _crypt(
        json.dumps(
            {"dataset_id": client.dataset_id, "original": current, "target_category": category}
        ).encode()
    )
    with backup_path.open("xb") as stream:
        stream.write(encrypted)
    headers = {
        "Authorization": f"Bearer {client.auth.ensure_access_token()}",
        "Qcs-Dataset-Id": client.dataset_id,
        "app-client-id": CLIENT_ID,
        "app-release": APP_RELEASE,
        "app-build": APP_BUILD,
        "accept": "application/json",
    }
    with SingleCategorySession(current, category) as session:
        response = session.request("PUT", session._url, json=payload, headers=headers)
    # Never automatically replay a write, including after 401 or a timeout.
    if not 200 <= response.status_code < 300:
        raise SimplifiHTTPError(response.status_code, "category update", "Response suppressed")
    verified = client.transaction(current["id"])
    if verified.get("coa") != payload["coa"]:
        raise SimplifiError("Category update was sent but the read-back category did not match")
    changes = {key for key in set(current) | set(verified) if current.get(key) != verified.get(key)}
    unexpected = changes - {"coa", "modifiedAt", "dbVersion", "userModifiedAt"}
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
    return verified
