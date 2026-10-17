import copy
import json
from unittest.mock import Mock

import pytest
import responses

from simplifi.client import Client
from simplifi.errors import ReadOnlyViolation, SimplifiError
from simplifi.transaction_rules import (
    PREVIEW_PATH,
    RuleRequestSession,
    apply_rule_update,
    digest,
    load_encrypted,
    prepare_rule_update,
    rule_payload,
)

ORIGINAL = {
    "id": "rule-1",
    "dbVersion": 4,
    "modifiedAt": "2026-01-01T00:00:00Z",
    "payeeSourceCondition": {
        "payeeType": "STATEMENT",
        "payeeMatchingCriteria": [
            {"payeeMatchingOperator": "CONTAINS", "payeeName": "example market"}
        ],
    },
    "coaTargetCondition": {"type": "CATEGORY", "id": "groceries"},
    "memoTarget": "original note",
    "applyToExistingTransactions": True,
}
PREVIEW = {
    "transactions": [{"id": "txn-1"}],
    "srcTxnsCount": 25,
    "resultTxnsCount": 1,
    "previewTxnsEndDate": "2025-09-01",
}
URL = "https://services.quicken.com"


def fake_client():
    client = Mock(dataset_id="dataset")
    client.auth.ensure_access_token.return_value = "secret-token"
    client.transaction_rule.return_value = copy.deepcopy(ORIGINAL)
    return client


def test_future_only_never_inherits_stored_backfill_flag():
    payload = rule_payload(ORIGINAL, {"memoTarget": "new note"})
    assert payload["applyToExistingTransactions"] is False
    assert payload["coaTargetCondition"] == ORIGINAL["coaTargetCondition"]
    assert ORIGINAL["applyToExistingTransactions"] is True
    assert "dbVersion" not in payload


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "other"},
        {"isDeleted": True},
        {"applyToExistingTransactions": True},
        {"isReviewedTarget": "yes"},
        {"amountSourceCondition": {"amountMatchingOperator": "EQUALS", "amount1": float("nan")}},
        {"payeeSourceCondition": {"payeeType": "STATEMENT", "payeeMatchingCriteria": []}},
    ],
)
def test_reject_unsupported_or_invalid_changes(changes):
    with pytest.raises(ReadOnlyViolation):
        rule_payload(ORIGINAL, changes)


@responses.activate
def test_rule_reads_use_known_paginated_endpoint(tmp_path):
    from simplifi.token_store import TokenStore

    client = Client(dataset_id="dataset", token_store=TokenStore(tmp_path / "tokens.dpapi"))
    client.auth.use_access_token("access")
    responses.get(URL + "/transaction-rules", json={"resources": [ORIGINAL], "metaData": {}})
    assert client.transaction_rule("rule-1") == ORIGINAL
    assert responses.calls[0].request.method == "GET"


@responses.activate
def test_prepare_only_previews_and_saves_encrypted_snapshot(tmp_path):
    client = fake_client()
    responses.post(URL + PREVIEW_PATH, json=PREVIEW)
    path = tmp_path / "plan.dpapi"
    result = prepare_rule_update(client, ORIGINAL, {"memoTarget": "new note"}, plan_path=path)
    assert len(responses.calls) == 1 and responses.calls[0].request.method == "POST"
    sent = json.loads(responses.calls[0].request.body)
    assert sent["applyToExistingTransactions"] is False
    assert b"example market" not in path.read_bytes() and b"new note" not in path.read_bytes()
    plan = load_encrypted(path)
    assert plan["original"] == ORIGINAL and result["confirmation"] == digest(plan)
    assert result["matching_transaction_count"] == 1
    assert result["preview_is_exhaustive"] is False


@responses.activate
def test_update_uses_preview_confirmation_backup_and_readback(tmp_path):
    client = fake_client()
    responses.post(URL + PREVIEW_PATH, json=PREVIEW)
    path = tmp_path / "plan.dpapi"
    result = prepare_rule_update(client, ORIGINAL, {"memoTarget": "new note"}, plan_path=path)
    updated = {**ORIGINAL, "memoTarget": "new note", "applyToExistingTransactions": False}
    client.transaction_rule.side_effect = [ORIGINAL, updated]
    responses.put(URL + "/transaction-rules/rule-1", json={"resource": updated})
    backup = tmp_path / "backup.dpapi"
    verified = apply_rule_update(
        client, path, confirmation=result["confirmation"], backup_path=backup
    )
    assert verified == updated
    assert backup.exists() and b"original note" not in backup.read_bytes()
    assert [r.request.method for r in responses.calls] == ["POST", "POST", "PUT"]


@responses.activate
def test_saved_backfill_plan_requires_separate_explicit_authorization(tmp_path):
    client = fake_client()
    responses.post(URL + PREVIEW_PATH, json=PREVIEW)
    path = tmp_path / "plan.dpapi"
    result = prepare_rule_update(
        client, ORIGINAL, {"memoTarget": "new"}, plan_path=path, apply_to_existing=True
    )
    with pytest.raises(ReadOnlyViolation, match="separate explicit approval"):
        apply_rule_update(
            client, path, confirmation=result["confirmation"], backup_path=tmp_path / "backup.dpapi"
        )
    assert len(responses.calls) == 1


@responses.activate
@pytest.mark.parametrize("failure", ["confirmation", "dataset", "stale-rule", "stale-preview"])
def test_reject_stale_or_mismatched_approval_without_put(tmp_path, failure):
    client = fake_client()
    responses.post(URL + PREVIEW_PATH, json=PREVIEW)
    path = tmp_path / "plan.dpapi"
    result = prepare_rule_update(client, ORIGINAL, {"memoTarget": "new"}, plan_path=path)
    confirmation = result["confirmation"]
    if failure == "confirmation":
        confirmation = "wrong"
    elif failure == "dataset":
        client.dataset_id = "other"
    elif failure == "stale-rule":
        client.transaction_rule.return_value = {**ORIGINAL, "dbVersion": 5}
    elif failure == "stale-preview":
        responses.post(URL + PREVIEW_PATH, json={**PREVIEW, "transactions": []})
    with pytest.raises(SimplifiError):
        apply_rule_update(
            client, path, confirmation=confirmation, backup_path=tmp_path / "backup.dpapi"
        )
    assert all(r.request.method != "PUT" for r in responses.calls)
    assert not (tmp_path / "backup.dpapi").exists()


@responses.activate
def test_redirect_is_not_followed_and_payload_scope_is_exact():
    payload = rule_payload(ORIGINAL)
    session = RuleRequestSession("POST", PREVIEW_PATH, payload)
    with pytest.raises(ReadOnlyViolation):
        session.request("POST", session.url, json={**payload, "memoTarget": "unapproved"})
    responses.post(URL + PREVIEW_PATH, status=302, headers={"Location": "https://evil.example"})
    with pytest.raises(SimplifiError):
        session.request("POST", session.url, json=payload)
    assert len(responses.calls) == 1
    with pytest.raises(ReadOnlyViolation):
        session.request("POST", session.url, json=payload)
