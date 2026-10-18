import json
from unittest.mock import Mock

import pytest
import responses

from simplifi.category_update import transfer_classification_payload, update_transfer_classification
from simplifi.errors import ReadOnlyViolation, SimplifiError

ORIGINAL = {
    "id": "rollover-1",
    "accountId": "retirement",
    "type": "CASH_FLOW",
    "amount": 123.45,
    "coa": {"type": "CATEGORY", "id": "income"},
    "payee": "Synthetic rollover",
    "postedOn": "2026-03-16",
    "isExcludedFromReports": True,
    "isExcludedFromF2S": True,
}


@pytest.mark.parametrize(
    "amount,incoming,expected",
    [(123.45, True, 123.45), (-123.45, True, 123.45), (123.45, False, -123.45)],
)
def test_direction_is_explicit_and_magnitude_preserved(amount, incoming, expected):
    original = {**ORIGINAL, "amount": amount}
    payload = transfer_classification_payload(original, incoming=incoming)
    assert payload == {
        **original,
        "amount": expected,
        "coa": {"type": "BALANCE_ADJUSTMENT", "id": "2"},
    }
    assert original["coa"]["type"] == "CATEGORY"


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "INVESTMENT"},
        {"transfer": {"txnId": "other"}},
        {"split": {"items": [1]}},
        {"isDeleted": True},
        {"amount": float("nan")},
        {"amount": 0},
        {"amount": True},
        {"coa": {"type": "ACCOUNT", "id": "other"}},
        {"id": "../other"},
    ],
)
def test_rejects_unsupported_records(changes):
    with pytest.raises(ReadOnlyViolation):
        transfer_classification_payload({**ORIGINAL, **changes}, incoming=True)


@responses.activate
def test_one_existing_transaction_no_counterpart_created(tmp_path):
    updated = transfer_classification_payload(ORIGINAL, incoming=True)
    client = Mock(dataset_id="dataset")
    client.transaction.side_effect = [ORIGINAL, updated]
    client.auth.ensure_access_token.return_value = "synthetic-token"
    responses.put("https://services.quicken.com/transactions/rollover-1", json={})
    result = update_transfer_classification(
        client, ORIGINAL, incoming=True, backup_path=tmp_path / "before.dpapi"
    )
    assert result == updated
    assert len(responses.calls) == 1
    assert json.loads(responses.calls[0].request.body) == updated
    assert b"Synthetic rollover" not in (tmp_path / "before.dpapi").read_bytes()


def test_concurrent_change_stops_before_backup_or_write(tmp_path):
    client = Mock()
    client.transaction.return_value = {**ORIGINAL, "amount": 999}
    with pytest.raises(SimplifiError, match="changed since selection"):
        update_transfer_classification(
            client, ORIGINAL, incoming=True, backup_path=tmp_path / "before.dpapi"
        )
    assert not (tmp_path / "before.dpapi").exists()


@responses.activate
def test_unexpected_amount_in_readback_stops(tmp_path):
    updated = transfer_classification_payload(ORIGINAL, incoming=True)
    client = Mock(dataset_id="dataset")
    client.transaction.side_effect = [ORIGINAL, {**updated, "amount": -123.45}]
    client.auth.ensure_access_token.return_value = "synthetic-token"
    responses.put("https://services.quicken.com/transactions/rollover-1", json={})
    with pytest.raises(SimplifiError, match="read-back"):
        update_transfer_classification(
            client, ORIGINAL, incoming=True, backup_path=tmp_path / "before.dpapi"
        )
    assert len(responses.calls) == 1
