"""Future-only create contract and conservative recovery tests, without network."""

import copy
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from simplifi.errors import ReadOnlyViolation, SimplifiError
from simplifi.journal_store import JournalStore
from simplifi.rule_creation import (
    RuleCreationPlan,
    creation_payload,
    execute_rule_creation,
    inspect_rule_creation,
    prepare_rule_creation,
)
from simplifi.transaction_rules import RuleRequestSession, load_encrypted

CHANGES = {
    "payeeSourceCondition": {
        "payeeType": "STATEMENT",
        "payeeMatchingCriteria": [
            {"payeeMatchingOperator": "CONTAINS", "payeeName": "Synthetic Merchant"}
        ],
    },
    "coaTargetCondition": {"type": "CATEGORY", "id": "food"},
    "payeeTarget": "Synthetic Merchant",
    "isExcludedFromReportsTarget": False,
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        Mock(side_effect=AssertionError("No live requests in tests")),
    )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    rules = {
        "existing": {
            "id": "existing",
            "payeeSourceCondition": {
                "payeeType": "STATEMENT",
                "payeeMatchingCriteria": [
                    {"payeeMatchingOperator": "CONTAINS", "payeeName": "Other Merchant"}
                ],
            },
            "coaTargetCondition": {"type": "CATEGORY", "id": "food"},
        }
    }
    client = Mock(dataset_id="dataset")
    client.transaction_rules.side_effect = lambda: copy.deepcopy(list(rules.values()))
    client.categories.return_value = [{"id": "food", "name": "Dining"}]
    client.accounts.return_value = [{"id": "account"}]
    client.transactions.return_value = [
        {"id": "transaction", "amount": -5, "coa": {"type": "CATEGORY", "id": "old"}}
    ]
    calls = []

    def request(client, method, path, payload):
        calls.append((method, path, copy.deepcopy(payload)))
        if path.endswith("/txn-rule"):
            return {"transactions": [], "srcTxnsCount": 0, "resultTxnsCount": 0}
        rules["created"] = {**copy.deepcopy(payload), "id": "created"}
        return {"resource": rules["created"], "id": "created"}

    monkeypatch.setattr("simplifi.rule_creation._request", request)
    path = tmp_path / "create.dpapi"
    preview = prepare_rule_creation(client, CHANGES, plan_path=path)
    return (
        client,
        rules,
        calls,
        request,
        path,
        {"confirmation": preview["confirmation"], "journal_dir": tmp_path / "journal"},
    )


def test_future_only_creation_success_and_resume(setup):
    client, rules, calls, _, path, kw = setup
    baseline = copy.deepcopy(rules["existing"])
    execute_rule_creation(client, path, **kw)
    execute_rule_creation(client, path, **kw)
    posts = [call for call in calls if call[1] == "/transaction-rules"]
    assert len(posts) == 1
    assert posts[0][2]["applyToExistingTransactions"] is False
    assert "id" not in posts[0][2]
    assert rules["existing"] == baseline
    assert rules["created"]["clientId"] == posts[0][2]["clientId"]


@pytest.mark.parametrize("succeeded", [True, False])
def test_uncertain_creation_never_replayed(setup, monkeypatch, succeeded):
    client, _, calls, request, path, kw = setup

    def uncertain(client, method, endpoint, payload):
        if endpoint == "/transaction-rules":
            if succeeded:
                request(client, method, endpoint, payload)
            else:
                calls.append((method, endpoint, payload))
            raise SimplifiError("Unknown creation outcome")
        return request(client, method, endpoint, payload)

    monkeypatch.setattr("simplifi.rule_creation._request", uncertain)
    with pytest.raises(SimplifiError):
        execute_rule_creation(client, path, **kw)
    if succeeded:
        execute_rule_creation(client, path, **kw)
    else:
        with pytest.raises(SimplifiError):
            execute_rule_creation(client, path, **kw)
    assert len([c for c in calls if c[1] == "/transaction-rules"]) == 1


@pytest.mark.parametrize(
    "fault", ["duplicate", "identity", "action", "backfill", "unrelated", "history"]
)
def test_ambiguous_or_unapproved_creation_stops(setup, monkeypatch, fault):
    client, rules, calls, request, path, kw = setup

    def changed(client, method, endpoint, payload):
        result = request(client, method, endpoint, payload)
        if endpoint == "/transaction-rules":
            if fault == "duplicate":
                rules["second"] = {**rules["created"], "id": "second"}
            if fault == "identity":
                rules["created"].pop("clientId")
            if fault == "action":
                rules["created"]["payeeTarget"] = "Wrong"
            if fault == "backfill":
                rules["created"]["applyToExistingTransactions"] = True
            if fault == "unrelated":
                rules["existing"]["memoTarget"] = "Changed"
            if fault == "history":
                client.transactions.return_value[0]["coa"] = {"type": "CATEGORY", "id": "food"}
        return result

    monkeypatch.setattr("simplifi.rule_creation._request", changed)
    for _ in range(2):
        with pytest.raises(SimplifiError):
            execute_rule_creation(client, path, **kw)
    assert len([c for c in calls if c[1] == "/transaction-rules"]) == 1


def test_exact_duplicate_conditions_rejected_before_preview(setup, tmp_path):
    client, rules, calls, _, _, _ = setup
    rules["existing"].update(copy.deepcopy(CHANGES))
    count = len(calls)
    with pytest.raises(ReadOnlyViolation):
        prepare_rule_creation(client, CHANGES, plan_path=tmp_path / "duplicate.dpapi")
    assert len(calls) == count


def test_changed_baseline_preview_dataset_or_category_prevents_create(setup, monkeypatch):
    client, rules, calls, _, path, kw = setup
    client.dataset_id = "wrong"
    with pytest.raises(ReadOnlyViolation):
        execute_rule_creation(client, path, **kw)
    client.dataset_id = "dataset"
    rules["existing"]["memoTarget"] = "Changed"
    with pytest.raises(SimplifiError):
        execute_rule_creation(client, path, **kw)
    rules["existing"].pop("memoTarget")
    client.categories.return_value = []
    with pytest.raises(SimplifiError):
        execute_rule_creation(client, path, **kw)
    client.categories.return_value = [{"id": "food", "name": "Dining"}]
    monkeypatch.setattr(
        "simplifi.rule_creation._preview", Mock(return_value={"transactions": [{}]})
    )
    with pytest.raises(SimplifiError):
        execute_rule_creation(client, path, **kw)
    assert not [c for c in calls if c[1] == "/transaction-rules"]


def test_locks_and_corrupt_journal_block_creation(setup):
    client, _, calls, _, path, kw = setup
    with JournalStore(kw["journal_dir"]).locked(), pytest.raises(SimplifiError):
        execute_rule_creation(client, path, **kw)
    execute_rule_creation(client, path, **kw)
    (kw["journal_dir"] / "checkpoint-00000001.dpapi").unlink()
    with pytest.raises(SimplifiError):
        execute_rule_creation(client, path, **kw)
    assert len([c for c in calls if c[1] == "/transaction-rules"]) == 1


def test_rule_insertion_checks_existing_adjacency(setup, monkeypatch):
    client, rules, _, request, path, kw = setup

    def ordered(client, method, endpoint, payload):
        result = request(client, method, endpoint, payload)
        if endpoint == "/transaction-rules":
            rules["created"]["prevTransactionRuleId"] = "existing"
            rules["existing"]["nextTransactionRuleId"] = "created"
            rules["existing"]["modifiedAt"] = "after"
        return result

    monkeypatch.setattr("simplifi.rule_creation._request", ordered)
    assert execute_rule_creation(client, path, **kw)["complete"]


def test_payload_metadata_and_historical_application_cannot_be_injected(setup):
    _, _, _, _, path, _ = setup
    original = load_encrypted(path)
    for field, value in [
        ("applyToExistingTransactions", True),
        ("applyToExistingTransactions", 0),
        ("id", "injected"),
        ("clientId", "../bad"),
    ]:
        wire = copy.deepcopy(original)
        wire["operations"][0]["payload"][field] = value
        with pytest.raises((ValidationError, ReadOnlyViolation)):
            RuleCreationPlan.model_validate(wire)
    with pytest.raises(ReadOnlyViolation):
        creation_payload({**CHANGES, "createdAt": "injected"}, client_id="synthetic-client")


def test_scoped_creation_request_exactly_once_and_no_backfill(monkeypatch):
    payload = creation_payload(CHANGES, client_id="synthetic-client")
    session = RuleRequestSession("POST", "/transaction-rules", payload)
    with pytest.raises(ReadOnlyViolation):
        session.request("POST", session.url, json={**payload, "applyToExistingTransactions": True})
    send = Mock(return_value=Mock(status_code=200, content=b"{}", json=dict))
    monkeypatch.setattr("requests.sessions.Session.request", send)
    session.request("POST", session.url, json=payload)
    with pytest.raises(ReadOnlyViolation):
        session.request("POST", session.url, json=payload)
    assert send.call_count == 1
    assert send.call_args.kwargs["allow_redirects"] is False
    with pytest.raises(ReadOnlyViolation):
        RuleRequestSession(
            "POST", "/transaction-rules", {**payload, "applyToExistingTransactions": True}
        )


def test_creation_review_and_status_are_offline(setup, monkeypatch):
    from simplifi.rules_cli import main

    _, _, _, _, path, kw = setup
    forbidden = Mock(side_effect=AssertionError("Offline command"))
    monkeypatch.setattr("simplifi.rules_cli.Client", forbidden)
    main(["create-review", "--plan", str(path)])
    main(["status", "--journal", str(kw["journal_dir"])])
    forbidden.assert_not_called()
    assert inspect_rule_creation(path)["apply_to_existing"] is False


def test_create_success_before_receipt_persistence_is_not_replayed(setup, monkeypatch):
    from simplifi.rule_creation import RuleCreationJournal

    client, _, calls, _, path, kw = setup
    checkpoint = RuleCreationJournal.checkpoint
    count = 0

    def interrupted(self, state):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("Crash before receipt persistence")
        return checkpoint(self, state)

    monkeypatch.setattr(RuleCreationJournal, "checkpoint", interrupted)
    with pytest.raises(OSError):
        execute_rule_creation(client, path, **kw)
    monkeypatch.setattr(RuleCreationJournal, "checkpoint", checkpoint)
    execute_rule_creation(client, path, **kw)
    assert len([c for c in calls if c[1] == "/transaction-rules"]) == 1
