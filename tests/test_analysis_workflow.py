"""Synthetic account-aware analysis, cash-trail, and resumable review sync tests."""

import copy
from datetime import date
from io import StringIO
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from simplifi.errors import SimplifiError, SimplifiHTTPError
from simplifi.journal_store import JournalStore
from simplifi.reconciliation import reconcile_transaction
from simplifi.review_management import confirm_records, refreshed_wire, show_finding
from simplifi.review_models import Review, Snapshot
from simplifi.review_preferences import ReviewPreferences
from simplifi.review_sync import reanalyze_review, set_review_preferences, sync_review
from simplifi.reviews import analyze, create_review, load_review, render_review, review_summary
from simplifi.transaction_plans import execute_transaction_plan, prepare_transaction_plan


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(
        "requests.sessions.Session.request", Mock(side_effect=AssertionError("Offline tests"))
    )


def row(tid, amount=-10, account="a1", category="food", payee="Example", day="2027-01-05"):
    return {
        "id": tid,
        "accountId": account,
        "type": "CASH_FLOW",
        "amount": amount,
        "coa": {"type": "CATEGORY", "id": category},
        "payee": payee,
        "postedOn": day,
        "cpData": {"id": "bank-" + tid, "payee": payee},
    }


def review(rows):
    snapshot = Snapshot(
        dataset_id="dataset",
        downloaded_at="2027-01-09T00:00:00+00:00",
        start=date(2027, 1, 1),
        end=date(2027, 12, 31),
        transactions=rows,
        categories=[{"id": "food", "name": "Dining"}, {"id": "other", "name": "Other"}],
        accounts=[
            {"id": "a1", "currency": "USD", "type": "BANK"},
            {"id": "a2", "currency": "USD", "type": "BANK"},
            {"id": "awards", "currency": "USD", "type": "INVESTMENT"},
            {"id": "trust", "currency": "USD", "type": "INVESTMENT"},
            {"id": "wallet", "currency": "USD", "type": "BANK"},
            {"id": "foreign", "currency": "EUR", "type": "BANK"},
        ],
    )
    return Review(id="review", original=snapshot, current=snapshot, findings=analyze(snapshot))


@pytest.fixture
def setup(tmp_path):
    r = review([row("t1"), row("t2", category="other")])
    directory = tmp_path / "review"
    create_review(directory, r)
    store = {t["id"]: copy.deepcopy(t) for t in r.current.transactions}
    client = Mock(dataset_id="dataset")
    client.transactions.side_effect = lambda **kwargs: copy.deepcopy(list(store.values()))
    client.transaction.side_effect = lambda tid: copy.deepcopy(store[tid])
    client.categories.return_value = r.current.categories
    client.accounts.return_value = r.current.accounts
    return directory, client, store


def test_cash_trail_retains_ambiguity_and_investment_actions():
    rows = [
        {
            **row("sale", 42.10, "awards", day="2027-01-01"),
            "type": "INVESTMENT",
            "investmentTxnType": "SELL",
        },
        row("journal-out", -42.10, "awards", day="2027-01-02"),
        row("journal-in", 42.10, "a1", day="2027-01-02"),
        {
            **row("out", -42.10, "a1", day="2027-01-03"),
            "transfer": {"id": "in"},
            "coa": {"type": "ACCOUNT", "id": "trust"},
        },
        {
            **row("in", 42.10, "trust", day="2027-01-04"),
            "transfer": {"id": "out"},
            "coa": {"type": "ACCOUNT", "id": "a1"},
        },
        row("ambiguous", 42.10, "a2", day="2027-01-02"),
        row("different-cent", -42.11, "a2"),
        row("different-currency", 42.10, "foreign"),
    ]
    r = review(rows)
    before = r.to_wire()
    result = reconcile_transaction(r, "journal-in", max_paths=2)
    assert result["automatic_action"] is False and result["ambiguous"]
    assert result["trails_truncated"]
    assert any(e["kind"] == "linked_transfer" and e["verified_link"] for e in result["edges"])
    assert any(e["kind"] == "investment_cash_flow" for e in result["edges"])
    assert not {"different-cent", "different-currency"} & {
        n["transaction"]["id"] for n in result["nodes"]
    }
    assert r.to_wire() == before


def test_bank_funding_uses_explicit_account_and_source_context():
    bank = row("bank", -17, "a1", payee="Wallet funding")
    bank["cpData"]["payee"] = "WALLET PAYMENT"
    wrong_owner = {**copy.deepcopy(bank), "id": "other-owner", "accountId": "a2"}
    r = review([bank, wrong_owner, row("dinner", -17, "wallet", payee="Dinner")])
    wire = r.to_wire()
    wire["context"]["analysis_preferences"] = {
        "account_owners": {"a1": "owner-one", "a2": "owner-two", "wallet": "owner-one"},
        "payment_channels": [
            {
                "bank_account_ids": ["a1"],
                "payment_account_id": "wallet",
                "statement_contains": "wallet payment",
            }
        ],
    }
    result = reconcile_transaction(Review.model_validate(wire), "bank")
    payments = [e for e in result["edges"] if e["kind"] == "bank_funded_payment"]
    assert [(e["from"], e["to"]) for e in payments] == [("bank", "dinner")]
    assert not result["automatic_action"]


def test_zero_and_broken_link_do_not_infer_transfers():
    r = review([row("zero", 0)])
    assert not reconcile_transaction(r, "zero")["edges"]
    r = review([{**row("out"), "transfer": {"id": "missing"}}])
    assert reconcile_transaction(r, "out")["warnings"]
    with pytest.raises(SimplifiError):
        reconcile_transaction(r, "out", days=-1)


def test_owner_groups_and_conventions_reduce_false_candidates():
    snapshot = review([row("one"), row("two", account="a2", category="other")]).current
    pref = ReviewPreferences(account_owners={"a1": "owner-one", "a2": "owner-two"})
    assert not analyze(snapshot, preferences=pref)
    pref = ReviewPreferences(
        conventions=[
            {
                "id": "valid-mix",
                "payee_contains": "Example",
                "category_ids": ["food", "other"],
                "note": "Different purchase types",
            }
        ]
    )
    assert not analyze(snapshot, preferences=pref)
    pref = ReviewPreferences(
        conventions=[
            {
                "id": "food",
                "payee_contains": "Example",
                "category_ids": ["food"],
                "note": "Recorded preference",
            }
        ]
    )
    findings = analyze(snapshot, preferences=pref)
    assert any(
        f.evidence["analysis"] == "convention" and f.transaction_ids == ["two"] for f in findings
    )


def test_aliases_duplicate_category_names_and_investment_separation():
    rows = [row("one", payee="Normalized"), row("two", category="other", payee="Variant")]
    rows[0]["cpData"]["payee"] = "BANK CODE"
    pref = ReviewPreferences(
        payee_aliases={"Variant": "Normalized"}, statement_aliases={"Normalized": ["BANK CODE"]}
    )
    snapshot = review(rows).current
    snapshot = snapshot.model_copy(
        update={"categories": [{"id": "food", "name": "Same"}, {"id": "other", "name": "Same"}]}
    )
    findings = analyze(snapshot, preferences=pref)
    assert len(findings) == 1 and findings[0].evidence["analysis"] == "mixed"
    investment = {**row("journal", account="awards"), "investmentTxnType": "SELL"}
    snapshot = snapshot.model_copy(
        update={"transactions": [investment, row("normal", category="other")]}
    )
    assert not analyze(snapshot)


def test_sync_appends_members_preserves_numbers_history_and_original(setup):
    directory, client, store = setup
    r = load_review(directory)
    fid, number, rationale = r.findings[0].id, r.findings[0].number, r.findings[0].rationale
    confirm_records(directory, number, ["t1"], note="Already correct", decision_id="confirmed")
    immutable = (directory / "original.dpapi").read_bytes()
    store["t3"] = row("t3", category="other")
    result = sync_review(client, directory)
    assert result["added"] == 1 and not result["live_changes"]
    r = load_review(directory)
    assert r.findings[0].id == fid and r.findings[0].number == number
    assert r.findings[0].rationale == rationale and r.findings[0].transaction_ids == [
        "t1",
        "t2",
        "t3",
    ]
    assert r.context["added_originals"]["t3"] == store["t3"]
    assert review_summary(r)["confirmed"] == 1
    sync_review(client, directory)
    assert len(load_review(directory).findings) == len(r.findings)
    assert (directory / "original.dpapi").read_bytes() == immutable


def test_first_seen_evidence_survives_later_sync_and_report_generation(setup, tmp_path):
    directory, client, store = setup
    store["t3"] = row("t3", category="other")
    sync_review(client, directory)
    store["t3"]["coa"] = {"type": "CATEGORY", "id": "food"}
    sync_review(client, directory)
    r = load_review(directory)
    assert r.context["added_originals"]["t3"]["coa"]["id"] == "other"
    result = show_finding(directory, r.findings[0].number)
    entry = next(e for e in result["transactions"] if e["original_id"] == "t3")
    assert entry["original"]["coa"]["id"] == "other" and entry["current"]["coa"]["id"] == "food"
    render_review(directory, tmp_path / "report.html")
    assert "Other</td><td>Dining" in (tmp_path / "report.html").read_text()


def test_sync_changed_original_invalidates_confirmation_and_prepared_plan(setup, tmp_path):
    directory, client, store = setup
    number = load_review(directory).findings[0].number
    confirm_records(directory, number, ["t1"], note="Checked", decision_id="confirmed")
    path = tmp_path / "plan.dpapi"
    preview = prepare_transaction_plan(
        client, directory, plan_path=path, decision_ids=["confirmed"]
    )
    store["t1"]["memo"] = "Changed"
    sync_review(client, directory)
    assert review_summary(load_review(directory))["confirmed"] == 0
    with pytest.raises(SimplifiError):
        execute_transaction_plan(
            client,
            path,
            confirmation=preview["confirmation"],
            journal_dir=tmp_path / "journal",
            review_dir=directory,
        )


def test_delta_absence_is_not_deletion_but_tombstone_invalidates(setup):
    directory, client, _ = setup
    number = load_review(directory).findings[0].number
    confirm_records(directory, number, ["t1"], note="Checked")
    client.transactions.side_effect = lambda **kwargs: []
    sync_review(client, directory, modified_after="2027-01-01T00:00:00Z")
    assert review_summary(load_review(directory))["confirmed"] == 1
    client.transactions.side_effect = lambda **kwargs: [{"id": "t1", "isDeleted": True}]
    result = sync_review(client, directory, modified_after="2027-01-01T00:00:00Z")
    assert review_summary(load_review(directory))["confirmed"] == 0 and result["coverage_warning"]


def test_missing_record_is_blocked_without_guessing_replacement(setup):
    directory, client, store = setup
    number = load_review(directory).findings[0].number
    confirm_records(directory, number, ["t1"], note="Checked")
    client.transactions.side_effect = lambda **kwargs: [store["t2"]]
    client.transaction.side_effect = SimplifiHTTPError(404, "ignored", "ignored")
    result = sync_review(client, directory)
    r = load_review(directory)
    assert result["missing"] == 1 and review_summary(r)["blocked"] == 1
    assert not r.replacements and review_summary(r)["confirmed"] == 0
    assert any(t["id"] == "t1" for t in r.current.transactions)
    assert show_finding(directory, number)["transactions"][0]["issues"]


def test_sync_failure_is_atomic_and_respects_locks_and_dataset(setup, monkeypatch):
    directory, client, store = setup
    before = (directory / "review.dpapi").read_bytes()
    with JournalStore(directory / "execution").locked(), pytest.raises(SimplifiError):
        sync_review(client, directory)
    client.dataset_id = "wrong"
    with pytest.raises(SimplifiError):
        sync_review(client, directory)
    client.dataset_id = "dataset"
    store["t3"] = row("t3")
    monkeypatch.setattr("simplifi.reviews.os.replace", Mock(side_effect=OSError("Disk full")))
    with pytest.raises(OSError):
        sync_review(client, directory)
    assert (directory / "review.dpapi").read_bytes() == before


@pytest.mark.parametrize(
    "bad",
    [
        {"account_owners": {"unknown": "owner"}},
        {"conventions": [{"id": "x", "category_ids": ["food"], "note": "No scope"}]},
        {"payee_aliases": {"A": "B", "B": "A"}},
        {"unknown_field": True},
    ],
)
def test_preferences_validate_scope_and_keep_data_local(setup, bad):
    directory, _, _ = setup
    before = (directory / "review.dpapi").read_bytes()
    with pytest.raises((ValidationError, SimplifiError)):
        set_review_preferences(directory, bad)
    assert (directory / "review.dpapi").read_bytes() == before


def test_local_analysis_preferences_and_reconciliation_cli_never_construct_client(
    setup, monkeypatch
):
    from simplifi.review_cli import main

    directory, _, _ = setup
    forbidden = Mock(side_effect=AssertionError("Local command must stay offline"))
    monkeypatch.setattr("simplifi.review_cli.Client", forbidden)
    monkeypatch.setattr("sys.stdin", StringIO('{"account_owners":{"a1":"owner-one"}}'))
    main(["preferences-set", "--review", str(directory), "--stdin"])
    for cmd in ("preferences", "analyze"):
        main([cmd, "--review", str(directory)])
    main(["reconcile", "--review", str(directory), "--transaction-id", "t1"])
    forbidden.assert_not_called()
    assert b"owner-one" not in (directory / "review.dpapi").read_bytes()


def test_legacy_snapshot_wire_and_resolved_entries_stay_stable(setup):
    directory, _, _ = setup
    r = load_review(directory)
    legacy = r.current.to_wire()
    legacy.pop("accounts")
    assert Snapshot.model_validate(legacy).to_wire() == legacy
    number = r.findings[0].number
    confirm_records(directory, number, ["t1", "t2"], note="Intentional mixed categories")
    set_review_preferences(directory, {"account_owners": {"a1": "new-owner-context"}})
    count = len(load_review(directory).findings)
    reanalyze_review(directory)
    assert len(load_review(directory).findings) == count


@pytest.mark.parametrize("key,value", [("units", 0), ("securityId", "synthetic-security")])
def test_instrument_metadata_is_not_ordinary_merchant_spending(key, value):
    investment = row("investment", category="other")
    investment["cpData"][key] = value
    assert not analyze(review([row("cash"), investment]).current)


def test_duplicate_account_ids_are_rejected():
    wire = review([row("cash")]).current.to_wire()
    wire["accounts"].append(wire["accounts"][0])
    with pytest.raises(ValidationError, match="Duplicate account"):
        Snapshot.model_validate(wire)


def test_missing_expense_invalidates_dependent_credit_decision(setup):
    directory, _, _ = setup
    r = load_review(directory)
    confirm_records(directory, r.findings[0].number, ["t1"], note="Offset verified")
    r = load_review(directory)
    r.context["reimbursement_offsets"] = [{"credit_id": "t1", "expense_id": "t2"}]
    wire, _ = refreshed_wire(r, {}, invalidated=["t2"])
    updated = Review.model_validate(wire)
    assert review_summary(updated)["confirmed"] == 0
    assert not updated.context["reimbursement_offsets"]


@pytest.mark.parametrize(
    "field,value", [("amount", "NaN"), ("postedOn", "bad"), ("isDeleted", "false")]
)
def test_full_sync_validates_exact_lookup_before_persisting(setup, field, value):
    directory, client, store = setup
    before = (directory / "review.dpapi").read_bytes()
    client.transactions.side_effect = lambda **kwargs: [store["t2"]]
    client.transaction.side_effect = lambda tid: {**store[tid], field: value}
    with pytest.raises(SimplifiError):
        sync_review(client, directory)
    assert (directory / "review.dpapi").read_bytes() == before


def test_zero_reconciliation_uses_the_same_result_shape():
    zero = reconcile_transaction(review([row("zero", 0)]), "zero")
    ordinary = reconcile_transaction(review([row("cash")]), "cash")
    assert zero.keys() == ordinary.keys()
    assert zero["nodes"][0].keys() == ordinary["nodes"][0].keys()
    assert zero["nodes"][0]["transaction"]["id"] == "zero"
    assert zero["warnings"][0]["transaction_id"] == "zero"
    assert not zero["edges"] and not zero["ambiguous"]


@pytest.mark.parametrize("limits", [{"max_hops": True}, {"max_hops": 1.5}, {"max_paths": False}])
def test_reconciliation_rejects_noninteger_limits(limits):
    with pytest.raises(SimplifiError, match="Invalid trail limits"):
        reconcile_transaction(review([row("cash")]), "cash", **limits)


def test_reconciliation_flags_downstream_branching_and_depth_limit():
    rows = [
        row("anchor", account="a1"),
        row("middle", account="wallet"),
        row("choice-one", account="a2"),
        row("choice-two", account="trust"),
    ]
    rows[0]["cpData"]["payee"] = "FIRST PAYMENT"
    rows[1]["cpData"]["payee"] = "SECOND PAYMENT"
    r = review(rows)
    r.context["analysis_preferences"] = {
        "payment_channels": [
            {
                "bank_account_ids": ["a1"],
                "payment_account_id": "wallet",
                "statement_contains": "FIRST",
            },
            {
                "bank_account_ids": ["wallet"],
                "payment_account_id": "a2",
                "statement_contains": "SECOND",
            },
            {
                "bank_account_ids": ["wallet"],
                "payment_account_id": "trust",
                "statement_contains": "SECOND",
            },
        ]
    }
    result = reconcile_transaction(r, "anchor", max_hops=1)
    assert result["ambiguous"] and result["depth_limited"]
    assert result["trails"] == [["anchor", "middle"]]
    assert not result["trails_truncated"]


def test_matching_link_references_with_wrong_signs_are_not_verified():
    bank = row("bank", -10, "a1")
    wallet = row("wallet", -10, "wallet")
    for source, target in ((bank, wallet), (wallet, bank)):
        source["transfer"] = {"id": target["id"]}
        source["coa"] = {"type": "ACCOUNT", "id": target["accountId"]}
    bank["cpData"]["payee"] = "WALLET PAYMENT"
    r = review([bank, wallet])
    r.context["analysis_preferences"] = {
        "payment_channels": [
            {
                "bank_account_ids": ["a1"],
                "payment_account_id": "wallet",
                "statement_contains": "WALLET",
            }
        ]
    }
    result = reconcile_transaction(r, "bank")
    assert result["edges"] and not any(e["verified_link"] for e in result["edges"])
    assert len(result["warnings"]) == 2


def test_reconciliation_needs_account_identity_for_candidates():
    outgoing = row("out", -10)
    incoming = row("in", 10, "a2")
    outgoing.pop("accountId")
    result = reconcile_transaction(review([outgoing, incoming]), "out")
    assert not result["edges"]
    assert [n["transaction"]["id"] for n in result["nodes"]] == ["out"]


def test_conflicting_conventions_cannot_suppress_a_candidate():
    snapshot = review([row("cash")]).current
    pref = ReviewPreferences(
        conventions=[
            {
                "id": "food",
                "payee_contains": "Example",
                "category_ids": ["food"],
                "note": "General",
            },
            {
                "id": "other",
                "account_ids": ["a1"],
                "category_ids": ["other"],
                "note": "Account-specific",
            },
        ]
    )
    findings = analyze(snapshot, preferences=pref)
    assert len(findings) == 1 and findings[0].evidence["analysis"] == "convention"
    assert findings[0].transaction_ids == ["cash"]


def test_full_sync_does_not_recheck_confirmed_tombstones(setup):
    directory, client, store = setup
    client.transactions.side_effect = lambda **kwargs: [{"id": "t1", "isDeleted": True}]
    sync_review(client, directory, modified_after="2027-01-01T00:00:00Z")
    client.transactions.side_effect = lambda **kwargs: [store["t2"]]
    client.transaction.side_effect = AssertionError("Known tombstone needs no lookup")
    result = sync_review(client, directory)
    assert result["missing"] == 0
    client.transaction.assert_not_called()


def test_sync_history_records_only_changed_evidence(setup):
    directory, client, store = setup
    sync_review(client, directory)
    refresh = [h for h in load_review(directory).history if h["kind"] == "refresh"][-1]
    assert refresh["results"] == {} and refresh["refreshed_count"] == 2
    store["t1"]["memo"] = "New evidence"
    sync_review(client, directory)
    r = load_review(directory)
    refresh = [h for h in r.history if h["kind"] == "refresh"][-1]
    assert refresh["results"] == {"t1": store["t1"]}
    assert refresh["refreshed_count"] == 2
    assert "memo" not in r.original.transactions[0]


@pytest.mark.parametrize("cursor", [False, 17, "", "2027-01-01T00:00:00"])
def test_bad_sync_cursor_fails_before_api_reads(setup, cursor):
    directory, client, _ = setup
    with pytest.raises(SimplifiError, match="Modified-after"):
        sync_review(client, directory, modified_after=cursor)
    client.transactions.assert_not_called()
