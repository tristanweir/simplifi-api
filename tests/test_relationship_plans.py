"""Synthetic relationship contracts and interrupted-execution regressions."""

import copy
from datetime import date
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from simplifi.errors import ReadOnlyViolation, SimplifiError
from simplifi.journal_store import JournalStore
from simplifi.relationship_plans import (
    RelationshipPlan,
    RelationshipSession,
    execute_relationship_plan,
    inspect_relationship_plan,
    prepare_relationship_plan,
)
from simplifi.review_models import Finding, Review, Snapshot
from simplifi.reviews import (
    create_review,
    load_review,
    render_review,
    replace_encrypted,
    review_summary,
)
from simplifi.transaction_rules import load_encrypted, save_encrypted


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        Mock(side_effect=AssertionError("No live requests in tests")),
    )


def setup_review(tmp_path, *, linked=False, recurring=False):
    rows = [
        {
            "id": tid,
            "accountId": account,
            "type": "CASH_FLOW",
            "amount": amount,
            "coa": {"type": "CATEGORY", "id": "old"},
            "postedOn": posted,
            "payee": "Synthetic",
            "memo": "Keep these notes",
            "cpData": {"id": "bank-" + tid, "memo": "Original statement"},
            "source": "QCS_REFRESH",
            "isExcludedFromReports": True,
            "isExcludedFromF2S": False,
        }
        for tid, account, amount, posted in [
            ("t1", "a1", -10, "2027-01-05"),
            ("t2", "a2", 10, "2027-01-07"),
            ("other", "a1", -7, "2027-01-08"),
        ]
    ]
    if linked:
        rows[0].update(coa={"type": "ACCOUNT", "id": "a2"}, transfer={"id": "t2"})
        rows[1].update(coa={"type": "ACCOUNT", "id": "a1"}, transfer={"id": "t1"})
    if recurring:
        rows[0].update(stModelId="series", stDueOn="2027-01-05", isBill=True)
    snapshot = Snapshot(
        dataset_id="dataset",
        downloaded_at="2027-01-09",
        start=date(2027, 1, 1),
        end=date(2027, 12, 31),
        transactions=rows,
        categories=[{"id": "old", "name": "Other"}, {"id": "food", "name": "Dining"}],
    )
    directory = tmp_path / "review"
    create_review(
        directory,
        Review(
            id="review",
            original=snapshot,
            current=snapshot,
            findings=[
                Finding(
                    id="finding",
                    number=1,
                    title="Synthetic",
                    rationale="Review",
                    transaction_ids=["t1", "t2"],
                )
            ],
        ),
    )
    store = {t["id"]: copy.deepcopy(t) for t in rows}
    client = Mock(dataset_id="dataset")
    client.transaction.side_effect = lambda tid: copy.deepcopy(store[tid])
    client.transactions.side_effect = lambda: copy.deepcopy(list(store.values()))
    client.accounts.return_value = [
        {"id": "a1", "currency": "USD"},
        {"id": "a2", "currency": "USD"},
    ]
    client.categories.return_value = snapshot.categories
    client.scheduled_transactions.return_value = [
        {"id": "series", "payee": "Synthetic"},
        {"id": "other-series"},
    ]
    return directory, client, store


def prepare(directory, client, tmp_path, action):
    path = tmp_path / "relationship.dpapi"
    targets = (
        {"t1": {"type": "CATEGORY", "id": "food"}, "t2": {"type": "UNCATEGORIZED", "id": "0"}}
        if action == "unlink"
        else None
    )
    preview = prepare_relationship_plan(
        client,
        directory,
        action=action,
        transaction_ids=["t1"] if action == "unlink_recurring" else ["t1", "t2"],
        targets=targets,
        plan_path=path,
    )
    return path, {
        "confirmation": preview["confirmation"],
        "review_dir": directory,
        "journal_dir": tmp_path / "journal",
    }


def writer(store, calls, *, replacement=True, timeout=False):
    def send(client, op):
        calls.append(op.action)
        if op.action != "unlink_recurring":
            for p in op.payloads:
                store[p["id"]].update(copy.deepcopy(p))
        else:
            result = copy.deepcopy(store["t1"])
            for k in ("stModelId", "stDueOn", "isBill", "isSubscription"):
                result.pop(k, None)
            if replacement:
                store.pop("t1")
                result["id"] = "replacement"
            store[result["id"]] = result
        if timeout:
            raise SimplifiError("Timeout after server success")

    return send


@pytest.mark.parametrize("action", ["link", "unlink", "unlink_recurring"])
def test_success_reconciles_after_timeout_without_duplicate_writes(tmp_path, monkeypatch, action):
    directory, client, store = setup_review(
        tmp_path, linked=action == "unlink", recurring=action == "unlink_recurring"
    )
    immutable = (directory / "original.dpapi").read_bytes()
    path, kw = prepare(directory, client, tmp_path, action)
    calls = []
    monkeypatch.setattr("simplifi.relationship_plans._send", writer(store, calls, timeout=True))
    with pytest.raises(SimplifiError):
        execute_relationship_plan(client, path, **kw)
    execute_relationship_plan(client, path, **kw)
    execute_relationship_plan(client, path, **kw)
    assert calls == [action]
    assert (directory / "original.dpapi").read_bytes() == immutable
    r = load_review(directory)
    if action == "unlink_recurring":
        assert r.replacements == {"t1": "replacement"}
        assert not store["replacement"].get("stModelId")
    elif action == "link":
        assert store["t1"]["transfer"] == {"id": "t2"}
        assert store["t2"]["transfer"] == {"id": "t1"}
    else:
        assert store["t1"]["coa"] == {"type": "CATEGORY", "id": "food"}
        assert store["t2"]["coa"] == {"type": "UNCATEGORIZED", "id": "0"}
    for row in r.current.transactions:
        assert row["isExcludedFromReports"] is True
        assert row["isExcludedFromF2S"] is False
        assert row["memo"] == "Keep these notes"
    render_review(directory, directory / "report.html")


@pytest.mark.parametrize("action", ["link", "unlink", "unlink_recurring"])
def test_uncertain_unapplied_request_never_replays(tmp_path, monkeypatch, action):
    directory, client, _ = setup_review(
        tmp_path, linked=action == "unlink", recurring=action == "unlink_recurring"
    )
    path, kw = prepare(directory, client, tmp_path, action)
    send = Mock(side_effect=SimplifiError("Timeout before success known"))
    monkeypatch.setattr("simplifi.relationship_plans._send", send)
    for _ in range(2):
        with pytest.raises(SimplifiError):
            execute_relationship_plan(client, path, **kw)
    assert send.call_count == 1


@pytest.mark.parametrize("fault", ["one_side", "sign", "notes", "exclusions", "extra", "unrelated"])
def test_transfer_partial_or_unexpected_changes_stop_without_retry(tmp_path, monkeypatch, fault):
    directory, client, store = setup_review(tmp_path)
    path, kw = prepare(directory, client, tmp_path, "link")
    calls = []
    before = copy.deepcopy(store)
    apply = writer(store, calls)

    def send(client, op):
        apply(client, op)
        if fault == "one_side":
            store["t2"] = before["t2"]
        if fault == "sign":
            store["t1"]["amount"] = 10
        if fault == "notes":
            store["t1"]["memo"] = "Lost notes"
        if fault == "exclusions":
            store["t2"]["isExcludedFromReports"] = False
        if fault == "extra":
            store["new"] = {**store["t1"], "id": "new"}
        if fault == "unrelated":
            store["other"]["payee"] = "Changed"

    monkeypatch.setattr("simplifi.relationship_plans._send", send)
    for _ in range(2):
        with pytest.raises(SimplifiError):
            execute_relationship_plan(client, path, **kw)
    assert calls == ["link"]


@pytest.mark.parametrize("fault", ["missing", "ambiguous", "series", "identity", "memo"])
def test_recurring_replacement_requires_unique_evidence(tmp_path, monkeypatch, fault):
    directory, client, store = setup_review(tmp_path, recurring=True)
    path, kw = prepare(directory, client, tmp_path, "unlink_recurring")
    calls = []
    apply = writer(store, calls)

    def send(client, op):
        apply(client, op)
        if fault == "missing":
            store.pop("replacement")
        if fault == "ambiguous":
            store["another"] = {**store["replacement"], "id": "another"}
        if fault == "series":
            client.scheduled_transactions.return_value = []
        if fault == "identity":
            store["replacement"]["cpData"]["id"] = "wrong"
        if fault == "memo":
            store["replacement"]["memo"] = "Changed"

    monkeypatch.setattr("simplifi.relationship_plans._send", send)
    for _ in range(2):
        with pytest.raises(SimplifiError):
            execute_relationship_plan(client, path, **kw)
    assert calls == ["unlink_recurring"]
    assert not load_review(directory).replacements


def test_recurring_same_id_and_projection_crash(tmp_path, monkeypatch):
    directory, client, store = setup_review(tmp_path, recurring=True)
    path, kw = prepare(directory, client, tmp_path, "unlink_recurring")
    calls = []
    monkeypatch.setattr(
        "simplifi.relationship_plans._send", writer(store, calls, replacement=False)
    )
    from simplifi import relationship_plans as module

    project = module._project
    monkeypatch.setattr(module, "_project", Mock(side_effect=OSError("Disk full")))
    with pytest.raises(OSError):
        execute_relationship_plan(client, path, **kw)
    monkeypatch.setattr(module, "_project", project)
    execute_relationship_plan(client, path, **kw)
    assert calls == ["unlink_recurring"]
    assert not load_review(directory).replacements


@pytest.mark.parametrize("fault", ["amount", "split", "investment", "self", "currency", "stale"])
def test_invalid_pair_rejected_before_write(tmp_path, fault):
    directory, client, store = setup_review(tmp_path)
    if fault == "amount":
        store["t1"]["amount"] = -11
    if fault == "split":
        store["t1"]["split"] = {"items": []}
    if fault == "investment":
        store["t1"]["type"] = "INVESTMENT"
    if fault == "self":
        store["t2"]["accountId"] = "a1"
    if fault == "currency":
        client.accounts.return_value[1]["currency"] = "EUR"
    if fault != "stale":
        r = load_review(directory).to_wire()
        r["current"]["transactions"] = list(store.values())
        replace_encrypted(directory / "review.dpapi", r)
    else:
        store["t1"]["memo"] = "Changed"
    with pytest.raises(SimplifiError):
        prepare(directory, client, tmp_path, "link")


def test_wrong_dataset_changed_original_competition_and_tampered_payload(tmp_path, monkeypatch):
    directory, client, store = setup_review(tmp_path)
    path, kw = prepare(directory, client, tmp_path, "link")
    client.dataset_id = "wrong"
    with pytest.raises(ReadOnlyViolation):
        execute_relationship_plan(client, path, **kw)
    client.dataset_id = "dataset"
    with JournalStore(kw["journal_dir"]).locked(), pytest.raises(SimplifiError):
        execute_relationship_plan(client, path, **kw)
    store["t2"]["memo"] = "Changed"
    send = Mock()
    monkeypatch.setattr("simplifi.relationship_plans._send", send)
    with pytest.raises(SimplifiError):
        execute_relationship_plan(client, path, **kw)
    send.assert_not_called()
    wire = load_encrypted(path)
    wire["operations"][0]["payloads"][0]["amount"] = 100
    with pytest.raises(ValidationError):
        RelationshipPlan.model_validate(wire)


def test_exact_batch_request_one_use_and_offline_inspection(tmp_path, monkeypatch):
    directory, client, _ = setup_review(tmp_path)
    path, _ = prepare(directory, client, tmp_path, "link")
    op = RelationshipPlan.model_validate(load_encrypted(path)).operations[0]
    session = RelationshipSession(op)
    with pytest.raises(ReadOnlyViolation):
        session.request("POST", session.url, json=session.payload)
    calls = []

    def request(_session, method, url, **kw):
        calls.append((method, url, kw))
        return Mock(status_code=200)

    monkeypatch.setattr("requests.sessions.Session.request", request)
    session.request("PUT", session.url, json=session.payload)
    with pytest.raises(ReadOnlyViolation):
        session.request("PUT", session.url, json=session.payload)
    assert len(calls) == 1
    assert calls[0][1].endswith("/transactions")
    assert {p["id"] for p in calls[0][2]["json"]["resources"]} == {"t1", "t2"}
    assert all(p["type"] == "CASH_FLOW" for p in calls[0][2]["json"]["resources"])
    assert calls[0][2]["allow_redirects"] is False and calls[0][2]["verify"] is True
    assert inspect_relationship_plan(path)["creates_counterparts"] is False


def test_bad_encrypted_plan_stays_local(tmp_path):
    path = tmp_path / "bad.dpapi"
    save_encrypted(path, {"kind": "wrong"})
    with pytest.raises(ValidationError):
        inspect_relationship_plan(path)


def test_success_before_receipt_checkpoint_is_reconciled(tmp_path, monkeypatch):
    from simplifi.relationship_plans import RelationshipJournal

    directory, client, store = setup_review(tmp_path)
    path, kw = prepare(directory, client, tmp_path, "link")
    calls = []
    monkeypatch.setattr("simplifi.relationship_plans._send", writer(store, calls))
    checkpoint = RelationshipJournal.checkpoint
    count = 0

    def interrupted(self, state):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("Crash before verified receipt persistence")
        return checkpoint(self, state)

    monkeypatch.setattr(RelationshipJournal, "checkpoint", interrupted)
    with pytest.raises(OSError):
        execute_relationship_plan(client, path, **kw)
    monkeypatch.setattr(RelationshipJournal, "checkpoint", checkpoint)
    execute_relationship_plan(client, path, **kw)
    assert calls == ["link"]


def test_pending_transfer_normalization_preserves_credit_sign(tmp_path, monkeypatch):
    directory, client, store = setup_review(tmp_path)
    store["t2"]["source"] = "QCS_REFRESH_BANK_PENDING"
    r = load_review(directory).to_wire()
    r["current"]["transactions"] = list(store.values())
    replace_encrypted(directory / "review.dpapi", r)
    path, kw = prepare(directory, client, tmp_path, "link")
    apply = writer(store, [])

    def normalized(client, op):
        apply(client, op)
        store["t2"]["source"] = "USER_OWNED_BANK_PENDING"
        store["t2"]["cpData"].pop("id")

    monkeypatch.setattr("simplifi.relationship_plans._send", normalized)
    execute_relationship_plan(client, path, **kw)
    assert store["t2"]["amount"] == 10


def test_replacement_outcomes_count_one_logical_transaction(tmp_path, monkeypatch):
    directory, client, store = setup_review(tmp_path, recurring=True)
    r = load_review(directory).to_wire()
    r["history"].append({"kind": "execution", "outcomes": {"t1": "changed"}})
    replace_encrypted(directory / "review.dpapi", r)
    path, kw = prepare(directory, client, tmp_path, "unlink_recurring")
    monkeypatch.setattr("simplifi.relationship_plans._send", writer(store, []))
    execute_relationship_plan(client, path, **kw)
    assert review_summary(load_review(directory))["changed"] == 1


def test_relationship_inspect_cli_stays_offline(tmp_path, monkeypatch):
    from simplifi.review_cli import main

    directory, client, _ = setup_review(tmp_path)
    path, _ = prepare(directory, client, tmp_path, "link")
    forbidden = Mock(side_effect=AssertionError("Offline inspection"))
    monkeypatch.setattr("simplifi.review_cli.Client", forbidden)
    main(["relationship-inspect", "--plan", str(path)])
    forbidden.assert_not_called()
