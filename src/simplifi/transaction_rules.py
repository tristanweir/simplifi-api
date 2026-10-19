"""Transaction-rule reads, read-only previews, and explicitly approved single updates."""

from __future__ import annotations

import copy
import hashlib
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

PREVIEW_PATH = "/categories-aggregation/preview/txn-rule"
EDITABLE = frozenset(
    [
        "payeeSourceCondition",
        "amountSourceCondition",
        "coaSourceConditions",
        "accountSourceCondition",
        "usageSourceType",
        "payeeTarget",
        "coaTargetCondition",
        "businessCoaTargetCondition",
        "useDefaultAccountUsage",
        "tagsTargetIds",
        "memoTarget",
        "isExcludedFromF2STarget",
        "isExcludedFromReportsTarget",
        "isReviewedTarget",
        "businessTargetId",
    ]
)
PAYLOAD_FIELDS = EDITABLE | {
    "id",
    "clientId",
    "createdAt",
    "modifiedAt",
    "hashKey",
    "prevTransactionRuleId",
    "nextTransactionRuleId",
    "applyToExistingTransactions",
}
BOOL_FIELDS = {
    "useDefaultAccountUsage",
    "isExcludedFromF2STarget",
    "isExcludedFromReportsTarget",
    "isReviewedTarget",
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def save_encrypted(path: Path, value):
    path = Path(path)
    if path.suffix != ".dpapi":
        raise SimplifiError("Rule plans and backups require a .dpapi filename")
    encrypted = _crypt(json.dumps(value, allow_nan=False).encode())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(encrypted)
        stream.flush()
        os.fsync(stream.fileno())


def load_encrypted(path: Path):
    try:
        return json.loads(_crypt(Path(path).read_bytes(), decrypt=True))
    except (ValueError, OSError):
        raise SimplifiError("Encrypted rule plan could not be read; details suppressed") from None


def rule_payload(original, changes=None, *, apply_to_existing=False):
    if type(apply_to_existing) is not bool:
        raise ReadOnlyViolation("Historical application must be an explicit boolean")
    if original.get("isDeleted") or not re.fullmatch(
        r"[A-Za-z0-9_-]+", str(original.get("id", ""))
    ):
        raise ReadOnlyViolation("An existing active rule is required")
    changes = changes or {}
    if not isinstance(changes, dict) or set(changes) - EDITABLE:
        raise ReadOnlyViolation("Changes contain unsupported or read-only rule fields")
    payload = {k: copy.deepcopy(v) for k, v in original.items() if k in PAYLOAD_FIELDS}
    for key, value in changes.items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = copy.deepcopy(value)
    # Never inherit the historical-application flag from the saved rule.
    payload["applyToExistingTransactions"] = apply_to_existing
    for key in BOOL_FIELDS:
        if key in payload and type(payload[key]) is not bool:
            raise ReadOnlyViolation("Rule boolean action has an invalid value")
    payee = payload.get("payeeSourceCondition")
    if payee:
        criteria = payee.get("payeeMatchingCriteria", [])
        if payee.get("payeeType") not in {"STATEMENT", "INFERRED"} or not 1 <= len(criteria) <= 50:
            raise ReadOnlyViolation("Invalid payee match condition")
        if any(
            c.get("payeeMatchingOperator") not in {"CONTAINS", "EQUALS"}
            or not isinstance(c.get("payeeName"), str)
            or not c["payeeName"].strip()
            for c in criteria
        ):
            raise ReadOnlyViolation("Invalid payee matching criteria")
    amount = payload.get("amountSourceCondition")
    if amount:
        operator = amount.get("amountMatchingOperator")
        if operator not in {"EQUALS", "GREATER_THAN", "LESS_THAN", "BETWEEN", "ANY_AMOUNT"}:
            raise ReadOnlyViolation("Invalid amount operator")
        for key in (
            ["amount1", "amount2"]
            if operator == "BETWEEN"
            else ["amount1"]
            if operator != "ANY_AMOUNT"
            else []
        ):
            if type(amount.get(key)) not in {int, float} or not math.isfinite(amount[key]):
                raise ReadOnlyViolation("Invalid rule amount")
    if not any(
        payload.get(k)
        for k in (
            "payeeSourceCondition",
            "amountSourceCondition",
            "coaSourceConditions",
            "accountSourceCondition",
            "usageSourceType",
        )
    ):
        raise ReadOnlyViolation("A rule requires at least one match condition")
    actions = EDITABLE - {
        "payeeSourceCondition",
        "amountSourceCondition",
        "coaSourceConditions",
        "accountSourceCondition",
        "usageSourceType",
        "useDefaultAccountUsage",
    }
    if not any(k in payload for k in actions):
        raise ReadOnlyViolation("A rule requires at least one action")
    return payload


class RuleRequestSession(SecureSession):
    """One request, bound to one exact preview, approved update, or deletion."""

    def __init__(self, method, path, payload):
        super().__init__()
        if not (
            method == "POST"
            and path == PREVIEW_PATH
            or method == "POST"
            and path == "/transaction-rules"
            or method in {"PUT", "DELETE"}
            and re.fullmatch(r"/transaction-rules/[A-Za-z0-9_-]+", path)
            and (method != "DELETE" or payload is None)
        ):
            raise ReadOnlyViolation("Unsupported rule operation")
        if method == "POST" and path == "/transaction-rules":
            from simplifi.rule_creation import creation_payload

            if (
                not isinstance(payload, dict)
                or payload.get("applyToExistingTransactions") is not False
                or payload
                != creation_payload(
                    {
                        k: v
                        for k, v in payload.items()
                        if k not in {"clientId", "applyToExistingTransactions"}
                    },
                    client_id=payload.get("clientId"),
                )
            ):
                raise ReadOnlyViolation("Invalid future-only rule creation payload")
        self.method, self.url, self.payload, self.used = (
            method,
            BASE_URL + path,
            copy.deepcopy(payload),
            False,
        )

    def request(self, method, url, **kwargs):
        if (
            self.used
            or method != self.method
            or url != self.url
            or digest(kwargs.get("json")) != digest(self.payload)
            or kwargs.get("params")
            or kwargs.get("data")
        ):
            raise ReadOnlyViolation("Request differs from the scoped rule operation")
        self.used = True
        kwargs.update(verify=True, allow_redirects=False, timeout=60)
        try:
            response = requests.Session.request(self, method, url, **kwargs)
        except requests.RequestException:
            raise SimplifiError(
                "Rule request failed; read the rule before retrying a mutation"
            ) from None
        if not 200 <= response.status_code < 300:
            raise SimplifiHTTPError(response.status_code, "rule operation", "Response suppressed")
        if method == "DELETE":
            return None  # Some successful DELETE responses contain non-JSON bodies.
        try:
            return response.json() if response.content else None
        except ValueError:
            raise SimplifiError("Unexpected rule response; details suppressed") from None


def _request(client, method, path, payload):
    if not client.dataset_id:
        raise SimplifiError("Resolve a dataset before accessing rules")
    headers = {
        "Authorization": f"Bearer {client.auth.ensure_access_token()}",
        "Qcs-Dataset-Id": client.dataset_id,
        "app-client-id": CLIENT_ID,
        "app-release": APP_RELEASE,
        "app-build": APP_BUILD,
        "accept": "application/json",
    }
    with RuleRequestSession(method, path, payload) as session:
        body = {} if method == "DELETE" else {"json": payload}
        return session.request(method, session.url, headers=headers, **body)


def preview_rule(client, rule, changes=None, *, apply_to_existing=False):
    payload = rule_payload(rule, changes, apply_to_existing=apply_to_existing)
    preview = _request(client, "POST", PREVIEW_PATH, payload)
    if not isinstance(preview, dict) or not isinstance(preview.get("transactions"), list):
        raise SimplifiError("Unsupported rule preview response; no update attempted")
    return preview


def prepare_rule_update(client, original, changes, *, plan_path, apply_to_existing=False):
    from simplifi.rule_preview import preview_warnings

    if client.transaction_rule(original["id"]) != original:
        raise SimplifiError("Rule changed since selection; fetch it again")
    payload = rule_payload(original, changes, apply_to_existing=apply_to_existing)
    preview = preview_rule(client, original, changes, apply_to_existing=apply_to_existing)
    plan = {
        "version": 1,
        "dataset_id": client.dataset_id,
        "original": original,
        "payload": payload,
        "preview": preview,
    }
    # Snapshot + payload + preview are all encrypted; the digest binds approval to this plan.
    save_encrypted(Path(plan_path), plan)
    return {
        "confirmation": digest(plan),
        "changes": {
            k: {"before": original.get(k), "after": payload.get(k)}
            for k in EDITABLE
            if original.get(k) != payload.get(k)
        },
        "apply_to_existing": apply_to_existing,
        "preview_sample_count": len(preview["transactions"]),
        "source_transaction_count": preview.get("srcTxnsCount"),
        "matching_transaction_count": preview.get("resultTxnsCount"),
        "preview_boundary": preview.get("previewTxnsEndDate"),
        "preview_is_exhaustive": False,
        "warnings": preview_warnings(payload),
    }


def apply_rule_update(client, plan_path, *, confirmation, backup_path, allow_existing=False):
    plan = load_encrypted(Path(plan_path))
    if (
        plan.get("version") != 1
        or digest(plan) != confirmation
        or plan.get("dataset_id") != client.dataset_id
    ):
        raise ReadOnlyViolation("Approval does not match this rule plan and dataset")
    original, payload = plan["original"], plan["payload"]
    if payload.get("applyToExistingTransactions") and allow_existing is not True:
        raise ReadOnlyViolation(
            "Updating existing transactions requires separate explicit approval"
        )
    changes = {k: payload.get(k) for k in EDITABLE if original.get(k) != payload.get(k)}
    if (
        rule_payload(
            original, changes, apply_to_existing=payload.get("applyToExistingTransactions")
        )
        != payload
    ):
        raise ReadOnlyViolation("Rule plan contains unsupported changes")
    if client.transaction_rule(original["id"]) != original:
        raise SimplifiError("Rule changed after preview; prepare a new plan")
    # Recheck the match preview too: new imports may change the potential affected set.
    preview = preview_rule(
        client, original, changes, apply_to_existing=payload["applyToExistingTransactions"]
    )
    if digest(preview) != digest(plan["preview"]):
        raise SimplifiError("Preview changed since review; prepare a new plan")
    save_encrypted(Path(backup_path), plan)
    _request(client, "PUT", f"/transaction-rules/{original['id']}", payload)
    verified = client.transaction_rule(original["id"])
    comparison = EDITABLE | {
        "applyToExistingTransactions",
        "prevTransactionRuleId",
        "nextTransactionRuleId",
    }
    if any(verified.get(k) != payload.get(k) for k in comparison):
        raise SimplifiError("Rule update sent but read-back differs; inspect before retrying")
    return verified
