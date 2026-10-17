import copy
import json
from unittest.mock import Mock

import pytest
import responses

from simplifi.category_update import SingleCategorySession, category_payload, update_category
from simplifi.errors import ReadOnlyViolation, SimplifiError

ORIGINAL = {
    "id": "txn-1",
    "type": "CASH_FLOW",
    "amount": -12.34,
    "payee": "Example",
    "coa": {"type": "CATEGORY", "id": "old"},
    "dbVersion": 3,
    "modifiedAt": "before",
    "cpData": {"memo": "preserve"},
    "knownCategoryId": "known",
    "accountId": "acct",
}
CATEGORY = {"id": "restaurants", "name": "Restaurants"}


def test_payload_changes_only_category_and_excludes_read_metadata():
    payload = category_payload(ORIGINAL, CATEGORY)
    assert payload["coa"] == {"type": "CATEGORY", "id": "restaurants"}
    assert payload["cpData"] == ORIGINAL["cpData"]
    assert payload["knownCategoryId"] == "known"
    assert "dbVersion" not in payload and "modifiedAt" not in payload
    assert ORIGINAL["coa"]["id"] == "old"


@responses.activate
def test_one_scoped_put_and_read_back(tmp_path):
    updated = copy.deepcopy(ORIGINAL)
    updated.update(coa={"type": "CATEGORY", "id": "restaurants"}, dbVersion=4, modifiedAt="after")
    client = Mock(dataset_id="dataset")
    client.transaction.side_effect = [ORIGINAL, updated]
    client.auth.ensure_access_token.return_value = "secret"
    responses.put("https://services.quicken.com/transactions/txn-1", status=200, json={})
    assert (
        update_category(client, ORIGINAL, CATEGORY, backup_path=tmp_path / "original.dpapi")
        == updated
    )
    assert b"Example" not in (tmp_path / "original.dpapi").read_bytes()
    assert len(responses.calls) == 1
    body = json.loads(responses.calls[0].request.body)
    assert body == category_payload(ORIGINAL, CATEGORY)


def test_scope_rejects_other_fields_and_transactions():
    session = SingleCategorySession(ORIGINAL, CATEGORY)
    payload = category_payload(ORIGINAL, CATEGORY)
    with pytest.raises(ReadOnlyViolation):
        session.request("PUT", session._url, json={**payload, "amount": -1})
    with pytest.raises(ReadOnlyViolation):
        session.request("PUT", "https://services.quicken.com/transactions/other", json=payload)


def test_concurrent_change_prevents_write(tmp_path):
    client = Mock()
    client.transaction.return_value = {**ORIGINAL, "dbVersion": 4}
    with pytest.raises(SimplifiError, match="changed since selection"):
        update_category(client, ORIGINAL, CATEGORY, backup_path=tmp_path / "backup.dpapi")
    assert not (tmp_path / "backup.dpapi").exists()
