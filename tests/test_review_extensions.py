"""Synthetic plan, flag-edit, receipt, and review-management regressions."""

import copy
from datetime import date
from io import StringIO
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from simplifi.category_update import (
    transaction_edit_payload,
    update_transaction_fields,
    verify_correction,
)
from simplifi.errors import ReadOnlyViolation, SimplifiError
from simplifi.journal_store import JournalStore
from simplifi.review_management import (
    attach_evidence,
    confirm_records,
    refresh_transactions,
    show_finding,
    unresolved_findings,
)
from simplifi.review_models import (
    Decision,
    Finding,
    Review,
    Snapshot,
    TransactionPlan,
    TransactionReceipt,
)
from simplifi.reviews import (
    create_review,
    load_review,
    record_decisions,
    render_review,
    review_summary,
)
from simplifi.transaction_plans import (
    execute_transaction_plan,
    import_transaction_receipt,
    inspect_transaction_receipt,
    prepare_transaction_plan,
)
from simplifi.transaction_rules import digest, load_encrypted, save_encrypted


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        Mock(side_effect=AssertionError("Tests must not use network")),
    )


@pytest.fixture
def review_setup(tmp_path):
    rows = [
        {
            "id": tid,
            "type": "CASH_FLOW",
            "accountId": "a1",
            "amount": amount,
            "postedOn": "2027-01-05",
            "payee": "Synthetic",
            "coa": {"type": "CATEGORY", "id": "old"},
            "isExcludedFromReports": True,
            "isExcludedFromF2S": True,
        }
        for tid, amount in [("t1", 42.50), ("t2", -42.50)]
    ]
    snapshot = Snapshot(
        dataset_id="dataset",
        downloaded_at="2027-01-06",
        start=date(2027, 1, 1),
        end=date(2027, 12, 31),
        transactions=rows,
        categories=[{"id": "old", "name": "Other"}, {"id": "new", "name": "Dining"}],
    )
    review = Review(
        id="review",
        original=snapshot,
        current=snapshot,
        findings=[
            Finding(
                id="f1",
                number=7,
                title="Synthetic",
                rationale="Review",
                transaction_ids=["t1", "t2"],
            )
        ],
    )
    directory = tmp_path / "review"
    create_review(directory, review)
    store = {t["id"]: copy.deepcopy(t) for t in rows}
    client = Mock(dataset_id="dataset")
    client.transaction.side_effect = lambda tid: copy.deepcopy(store[tid])
    client.categories.return_value = snapshot.categories
    return directory, client, store


def decision(directory, action="transfer", **extra):
    record_decisions(
        directory,
        [{"id": "d1", "finding_id": "f1", "transaction_ids": ["t1"], "action": action, **extra}],
    )


def plan(directory, client, tmp_path):
    path = tmp_path / "plan.dpapi"
    preview = prepare_transaction_plan(client, directory, plan_path=path)
    return (
        path,
        preview,
        {
            "confirmation": preview["confirmation"],
            "journal_dir": tmp_path / "journal",
            "review_dir": directory,
        },
    )


@pytest.mark.parametrize("incoming", [True, False])
def test_transfer_direction_preview_and_resume(review_setup, tmp_path, monkeypatch, incoming):
    directory, client, store = review_setup
    decision(directory, incoming=incoming)
    path, preview, kw = plan(directory, client, tmp_path)
    expected = 42.50 if incoming else -42.50
    assert preview["operations"][0]["after_amount"] == expected
    calls = []

    def writer(client, original, *, incoming, backup_path):
        calls.append(original["id"])
        store[original["id"]].update(coa={"type": "BALANCE_ADJUSTMENT", "id": "2"}, amount=expected)
        raise SimplifiError("Lost response after server success")

    monkeypatch.setattr("simplifi.transaction_plans.update_transfer_classification", writer)
    with pytest.raises(SimplifiError):
        execute_transaction_plan(client, path, **kw)
    execute_transaction_plan(client, path, **kw)
    assert calls == ["t1"]
    r = load_review(directory)
    assert r.current.transactions[0]["amount"] == expected
    assert r.current.transactions[0]["isExcludedFromReports"] is True
    assert r.original.transactions[0]["coa"]["id"] == "old"
    assert review_summary(r)["changed"] == 1


@pytest.mark.parametrize(
    "action,extra",
    [
        ("exclusions", {}),
        ("category", {"category_id": "new"}),
        ("transfer", {"incoming": True}),
    ],
)
def test_flags_compose_and_projection_recovers(review_setup, tmp_path, monkeypatch, action, extra):
    directory, client, store = review_setup
    decision(directory, action, exclusions={"isExcludedFromReports": False}, **extra)
    path, preview, kw = plan(directory, client, tmp_path)
    assert preview["operations"][0]["exclusions"]["isExcludedFromReports"]["after"] is False
    calls = []

    def writer(client, original, *, category, incoming, exclusions, backup_path):
        calls.append(original["id"])
        payload = transaction_edit_payload(
            original, category=category, incoming=incoming, exclusions=exclusions
        )
        store[original["id"]].update(payload)
        store[original["id"]].pop("isExcludedFromReports")

    monkeypatch.setattr("simplifi.transaction_plans.update_transaction_fields", writer)
    from simplifi import transaction_plans as module

    project = module._project
    monkeypatch.setattr(module, "_project", Mock(side_effect=OSError("Disk failure")))
    with pytest.raises(OSError):
        execute_transaction_plan(client, path, **kw)
    monkeypatch.setattr(module, "_project", project)
    execute_transaction_plan(client, path, **kw)
    execute_transaction_plan(client, path, **kw)
    assert calls == ["t1"]
    current = load_review(directory).current.transactions[0]
    assert current.get("isExcludedFromReports", False) is False
    assert current["isExcludedFromF2S"] is True
    assert current["amount"] == 42.50


@pytest.mark.parametrize(
    "extra",
    [
        {"action": "transfer"},
        {"action": "transfer", "incoming": "true"},
        {"action": "exclusions"},
        {"action": "confirm", "incoming": False},
        {"action": "confirm", "exclusions": {"isExcludedFromReports": True}},
        {"action": "exclusions", "exclusions": {"isExcludedFromReports": 1}},
        {"action": "exclusions", "exclusions": {"isExcludedFromBudgets": True}},
    ],
)
def test_malformed_changes_rejected(extra):
    with pytest.raises(ValidationError):
        Decision.model_validate({"id": "d", "finding_id": "f", "transaction_ids": ["t"], **extra})


@pytest.mark.parametrize(
    "change",
    [
        {"type": "INVESTMENT"},
        {"split": [{}]},
        {"transfer": {"id": "other"}},
        {"coa": {"type": "ACCOUNT", "id": "other"}},
        {"coa": {"type": "UNCATEGORIZED", "id": "unknown"}},
    ],
)
def test_flags_reject_unsupported_records(review_setup, change):
    _, _, store = review_setup
    with pytest.raises(ReadOnlyViolation):
        transaction_edit_payload(
            {**store["t1"], **change}, exclusions={"isExcludedFromReports": False}
        )


def test_zero_flag_edit_and_strict_readback(review_setup):
    _, _, store = review_setup
    original = {**store["t1"], "amount": 0}
    payload = transaction_edit_payload(original, exclusions={"isExcludedFromReports": False})
    result = copy.deepcopy(original)
    result.pop("isExcludedFromReports")
    verify_correction(original, payload, result, allowed_changes={"isExcludedFromReports"})
    for bad in (
        {**result, "isExcludedFromReports": 0},
        {**result, "amount": 1},
        {**result, "isExcludedFromF2S": False},
    ):
        with pytest.raises(SimplifiError):
            verify_correction(original, payload, bad, allowed_changes={"isExcludedFromReports"})


def test_changed_original_and_conflict_block_transfer(review_setup, tmp_path, monkeypatch):
    directory, client, store = review_setup
    decision(directory, incoming=True)
    record_decisions(
        directory,
        [
            {
                "id": "conflict",
                "finding_id": "f1",
                "transaction_ids": ["t1"],
                "action": "transfer",
                "incoming": False,
            }
        ],
    )
    with pytest.raises(ReadOnlyViolation):
        plan(directory, client, tmp_path)
    path = tmp_path / "selected.dpapi"
    preview = prepare_transaction_plan(client, directory, plan_path=path, decision_ids=["d1"])
    store["t1"]["amount"] = 99
    writer = Mock()
    monkeypatch.setattr("simplifi.transaction_plans.update_transfer_classification", writer)
    with pytest.raises(SimplifiError):
        execute_transaction_plan(
            client,
            path,
            confirmation=preview["confirmation"],
            journal_dir=tmp_path / "journal",
            review_dir=directory,
        )
    writer.assert_not_called()


def test_old_plan_canonical_shape_is_preserved(review_setup, tmp_path):
    directory, client, _ = review_setup
    decision(directory, "category", category_id="new")
    path, _, _ = plan(directory, client, tmp_path)
    wire = load_encrypted(path)
    assert "incoming" not in wire["operations"][0]
    assert "exclusions" not in wire["decisions"][0]
    assert TransactionPlan.model_validate(wire).to_wire() == wire
    assert digest(TransactionPlan.model_validate(wire).to_wire()) == digest(wire)


def test_review_commands_stay_offline_and_escape_evidence(
    review_setup, tmp_path, monkeypatch, capsys
):
    from simplifi.review_cli import main

    directory, _, _ = review_setup
    forbidden = Mock(side_effect=AssertionError("Local commands cannot construct client"))
    monkeypatch.setattr("simplifi.review_cli.Client", forbidden)
    main(["show", "--review", str(directory), "--number", "7"])
    main(["unresolved", "--review", str(directory)])
    for command in ("evidence", "confirm"):
        monkeypatch.setattr("sys.stdin", StringIO("<script>synthetic evidence</script>"))
        main(
            [
                command,
                "--review",
                str(directory),
                "--number",
                "7",
                "--transaction-id",
                "t1",
                "--note-stdin",
            ]
        )
    forbidden.assert_not_called()
    assert unresolved_findings(directory)[0]["transaction_ids"] == ["t2"]
    assert show_finding(directory, 7)["evidence"]
    render_review(directory, tmp_path / "report.html")
    text = (tmp_path / "report.html").read_text()
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "Synthetic" in capsys.readouterr().out
    with pytest.raises(SimplifiError):
        confirm_records(directory, 7, ["missing"], note="No")
    with pytest.raises(SimplifiError):
        attach_evidence(directory, 7, ["t1"], note=" ")


def test_refresh_invalidates_changed_confirmation_and_old_plan(review_setup, tmp_path):
    directory, client, store = review_setup
    confirm_records(directory, 7, ["t1"], note="Checked", decision_id="confirmed")
    path, _, kw = plan(directory, client, tmp_path)
    immutable = (directory / "original.dpapi").read_bytes()
    store["t1"]["memo"] = "New evidence"
    result = refresh_transactions(client, directory, ["t1"])
    assert result["changed"] == 1
    assert review_summary(load_review(directory))["confirmed"] == 0
    assert unresolved_findings(directory)[0]["transaction_ids"] == ["t1", "t2"]
    assert (directory / "original.dpapi").read_bytes() == immutable
    with pytest.raises(ReadOnlyViolation):
        execute_transaction_plan(client, path, **kw)
    client.dataset_id = "wrong"
    with pytest.raises(SimplifiError):
        refresh_transactions(client, directory, ["t1"])


def make_receipt(directory, client, store, tmp_path):
    decision(directory, incoming=True)
    path, _, _ = plan(directory, client, tmp_path)
    op = TransactionPlan.model_validate(load_encrypted(path)).operations[0]
    result = {**store["t1"], "coa": op.payload["coa"]}
    receipt = TransactionReceipt(
        dataset_id="dataset",
        review_id="review",
        verified_at="2027-01-06",
        operation=op,
        result=result,
    )
    target = tmp_path / "receipt.dpapi"
    save_encrypted(target, receipt.to_wire())
    return target, result


def test_external_receipt_import_is_read_only_and_idempotent(review_setup, tmp_path, monkeypatch):
    directory, client, store = review_setup
    receipt, result = make_receipt(directory, client, store, tmp_path)
    preview = inspect_transaction_receipt(receipt)
    store["t1"] = result
    kw = {"confirmation": preview["confirmation"], "finding_number": 7}
    assert import_transaction_receipt(client, directory, receipt, **kw)["imported"]
    assert import_transaction_receipt(client, directory, receipt, **kw)["already_imported"]
    r = load_review(directory)
    assert review_summary(r)["changed"] == 1
    assert review_summary(r)["confirmed"] == 1
    assert r.original.transactions[0]["coa"]["id"] == "old"


@pytest.mark.parametrize("fault", ["digest", "dataset", "live", "finding", "original"])
def test_external_receipt_rejects_unverified_evidence(review_setup, tmp_path, fault):
    directory, client, store = review_setup
    path, result = make_receipt(directory, client, store, tmp_path)
    preview = inspect_transaction_receipt(path)
    kw = {"confirmation": preview["confirmation"], "finding_number": 7}
    store["t1"] = result
    if fault == "digest":
        kw["confirmation"] = "wrong"
    if fault == "dataset":
        client.dataset_id = "wrong"
    if fault == "live":
        store["t1"]["memo"] = "Changed"
    if fault == "finding":
        kw["finding_number"] = 999
    if fault == "original":
        from simplifi.reviews import replace_encrypted

        r = load_review(directory).to_wire()
        r["current"]["transactions"][0]["memo"] = "Different original"
        replace_encrypted(directory / "review.dpapi", r)
    before = (directory / "review.dpapi").read_bytes()
    with pytest.raises(SimplifiError):
        import_transaction_receipt(client, directory, path, **kw)
    assert (directory / "review.dpapi").read_bytes() == before


def test_refresh_and_evidence_respect_execution_lock(review_setup):
    directory, client, _ = review_setup
    with JournalStore(directory / "execution").locked():
        with pytest.raises(SimplifiError):
            refresh_transactions(client, directory, ["t1"])
        with pytest.raises(SimplifiError):
            attach_evidence(directory, 7, ["t1"], note="Checked")


def test_scoped_flag_writer_preserves_fields_and_checks_readback(
    review_setup, tmp_path, monkeypatch
):
    _, client, store = review_setup
    original = store["t1"]
    payload = transaction_edit_payload(original, exclusions={"isExcludedFromReports": False})
    calls = []

    def send(session, method, url, **kwargs):
        calls.append((method, url, kwargs))
        store["t1"] = {**original, "isExcludedFromReports": False}
        return Mock(status_code=200)

    monkeypatch.setattr("requests.sessions.Session.request", send)
    result = update_transaction_fields(
        client,
        original,
        exclusions={"isExcludedFromReports": False},
        backup_path=tmp_path / "backup.dpapi",
    )
    assert result["isExcludedFromF2S"] is True
    assert result["amount"] == 42.50
    assert len(calls) == 1 and calls[0][2]["json"] == payload
    assert load_encrypted(tmp_path / "backup.dpapi")["original"] == original


def test_uncertain_flag_write_is_not_replayed(review_setup, tmp_path, monkeypatch):
    directory, client, _ = review_setup
    decision(directory, "exclusions", exclusions={"isExcludedFromReports": False})
    path, _, kw = plan(directory, client, tmp_path)
    writer = Mock(side_effect=SimplifiError("Uncertain before server change"))
    monkeypatch.setattr("simplifi.transaction_plans.update_transaction_fields", writer)
    with pytest.raises(SimplifiError):
        execute_transaction_plan(client, path, **kw)
    with pytest.raises(SimplifiError):
        execute_transaction_plan(client, path, **kw)
    assert writer.call_count == 1


def test_noop_flags_send_no_put(review_setup, tmp_path):
    _, client, store = review_setup
    original = store["t1"]
    result = update_transaction_fields(
        client,
        original,
        exclusions={"isExcludedFromReports": True},
        backup_path=tmp_path / "unused.dpapi",
    )
    assert result == original
    assert not (tmp_path / "unused.dpapi").exists()


def test_partial_refresh_retains_other_decisions_and_releases_offsets(review_setup):
    directory, client, store = review_setup
    decision(directory, "category", category_id="new", offsets={"t1": "t2"})
    store["t2"]["amount"] = -40
    refresh_transactions(client, directory, ["t2"])
    r = load_review(directory)
    assert not r.context["used_reimbursement_expense_ids"]
    assert not r.context["reimbursement_offsets"]
    assert unresolved_findings(directory)[0]["transaction_ids"] == ["t1", "t2"]


def test_expected_snapshot_prevents_stale_local_confirmation(review_setup):
    directory, _, _ = review_setup
    with pytest.raises(SimplifiError):
        record_decisions(
            directory,
            [{"id": "d", "finding_id": "f1", "transaction_ids": ["t1"], "action": "confirm"}],
            expected_current="stale",
        )
    assert not load_review(directory).decisions


def test_confirm_supersedes_only_selected_group_member(review_setup, tmp_path):
    directory, client, _ = review_setup
    record_decisions(
        directory,
        [
            {
                "id": "group",
                "finding_id": "f1",
                "transaction_ids": ["t1", "t2"],
                "action": "category",
                "category_id": "new",
            }
        ],
    )
    confirm_records(directory, 7, ["t1"], note="Already correct")
    r = load_review(directory)
    assert r.decisions[0].transaction_ids == ["t2"]
    path, _, _ = plan(directory, client, tmp_path)
    assert len(TransactionPlan.model_validate(load_encrypted(path)).operations) == 2


def test_confirm_preserves_accepted_offset_evidence(review_setup):
    directory, client, store = review_setup
    decision(directory, "category", category_id="new", offsets={"t1": "t2"})
    confirm_records(directory, 7, ["t1"], note="Checked")
    r = load_review(directory)
    assert r.context["used_reimbursement_expense_ids"] == ["t2"]
    assert r.context["reimbursement_offsets"][0]["credit_id"] == "t1"
    store["t2"]["amount"] = -40
    refresh_transactions(client, directory, ["t2"])
    assert load_review(directory).context["reimbursement_offsets"] == []
    assert review_summary(load_review(directory))["confirmed"] == 0
