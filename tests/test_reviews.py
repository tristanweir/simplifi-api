import copy
from datetime import date
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from simplifi.errors import ReadOnlyViolation, SimplifiError
from simplifi.journal_store import JournalStore
from simplifi.review_import import build_legacy_import, import_legacy_review
from simplifi.review_models import Finding, Review, Snapshot
from simplifi.reviews import (
    analyze,
    create_review,
    load_review,
    record_decisions,
    reimbursement_candidates,
    render_review,
    resolve_category,
    review_summary,
)
from simplifi.transaction_plans import (
    execute_transaction_plan,
    prepare_transaction_plan,
    transaction_plan_status,
)
from simplifi.transaction_rules import load_encrypted, save_encrypted

CATEGORY = {"id": "dining", "name": "Restaurants"}


def txn(tid="t1", amount=20, coa=None):
    return {
        "id": tid,
        "type": "CASH_FLOW",
        "amount": amount,
        "payee": "Example",
        "accountId": "a1",
        "postedOn": "2026-01-10",
        "coa": coa or {"type": "CATEGORY", "id": "old"},
    }


@pytest.fixture
def setup(tmp_path):
    rows = [txn(), txn("t2", -20)]
    snapshot = Snapshot(
        dataset_id="dataset",
        downloaded_at="2026-01-11",
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
        transactions=rows,
        categories=[CATEGORY, {"id": "old", "name": "Other"}],
    )
    review = Review(
        id="review",
        original=snapshot,
        current=snapshot,
        findings=[
            Finding(
                id="f1",
                number=57,
                title="<script>alert(1)</script>",
                rationale="Synthetic",
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


def decide(directory, **extra):
    value = {
        "id": "d1",
        "finding_id": "f1",
        "transaction_ids": ["t1", "t2"],
        "action": "category",
        "category_id": "dining",
        **extra,
    }
    return record_decisions(directory, [value])


def prepare(directory, client, tmp_path):
    path = tmp_path / "plan.dpapi"
    info = prepare_transaction_plan(client, directory, plan_path=path)
    return path, {
        "confirmation": info["confirmation"],
        "journal_dir": tmp_path / "journal",
        "review_dir": directory,
    }


def fake_writer(store, calls):
    def write(client, original, category, *, backup_path):
        calls.append(original["id"])
        store[original["id"]]["coa"] = {"type": "CATEGORY", "id": category["id"]}
        return copy.deepcopy(store[original["id"]])

    return write


def test_category_plan_resume_and_projection(setup, tmp_path, monkeypatch):
    directory, client, store = setup
    decide(directory, exceptions={"t2": "old"})
    path, kwargs = prepare(directory, client, tmp_path)
    calls = []
    monkeypatch.setattr("simplifi.transaction_plans.update_category", fake_writer(store, calls))
    execute_transaction_plan(client, path, **kwargs)
    execute_transaction_plan(client, path, **kwargs)
    assert calls == ["t1", "t2"]  # Synthetic writer receives no-op; scoped real writer does no PUT.
    assert store["t1"]["amount"] == 20
    summary = review_summary(load_review(directory))
    assert summary["changed"] == summary["already_correct"] == 1
    assert transaction_plan_status(kwargs["journal_dir"])["complete"]


@pytest.mark.parametrize("server_success", [True, False])
def test_uncertain_write_never_replayed(setup, tmp_path, monkeypatch, server_success):
    directory, client, store = setup
    decide(directory)
    path, kwargs = prepare(directory, client, tmp_path)
    calls = []
    writer = fake_writer(store, calls)

    def uncertain(*args, **kw):
        if server_success:
            writer(*args, **kw)
        else:
            calls.append(args[1]["id"])
        raise SimplifiError("Uncertain")

    monkeypatch.setattr("simplifi.transaction_plans.update_category", uncertain)
    with pytest.raises(SimplifiError):
        execute_transaction_plan(client, path, **kwargs)
    monkeypatch.setattr("simplifi.transaction_plans.update_category", writer)
    if server_success:
        execute_transaction_plan(client, path, **kwargs)
        assert calls == ["t1", "t2"]
    else:
        with pytest.raises(SimplifiError):
            execute_transaction_plan(client, path, **kwargs)
        assert calls == ["t1"]


def test_projection_crash_resumes_without_writes(setup, tmp_path, monkeypatch):
    directory, client, store = setup
    decide(directory)
    path, kwargs = prepare(directory, client, tmp_path)
    calls = []
    monkeypatch.setattr("simplifi.transaction_plans.update_category", fake_writer(store, calls))
    from simplifi import transaction_plans as module

    project = module._project
    monkeypatch.setattr(module, "_project", Mock(side_effect=OSError))
    with pytest.raises(OSError):
        execute_transaction_plan(client, path, **kwargs)
    monkeypatch.setattr(module, "_project", project)
    execute_transaction_plan(client, path, **kwargs)
    assert calls == ["t1", "t2"]
    assert review_summary(load_review(directory))["changed"] == 2


def test_preflight_checks_whole_batch(setup, tmp_path, monkeypatch):
    directory, client, store = setup
    decide(directory)
    path, kwargs = prepare(directory, client, tmp_path)
    store["t2"]["amount"] = -99
    writer = Mock()
    monkeypatch.setattr("simplifi.transaction_plans.update_category", writer)
    with pytest.raises(SimplifiError):
        execute_transaction_plan(client, path, **kwargs)
    writer.assert_not_called()
    client.dataset_id = "different"
    with pytest.raises(ReadOnlyViolation):
        execute_transaction_plan(client, path, **kwargs)


def test_decision_validation_and_conflicts(setup, tmp_path):
    directory, client, _ = setup
    with pytest.raises(ValidationError):
        decide(directory, exceptions={"missing": "old"})
    decide(directory)
    record_decisions(
        directory,
        [{"id": "d2", "finding_id": "f1", "transaction_ids": ["t1"], "action": "confirm"}],
    )
    with pytest.raises(ReadOnlyViolation):
        prepare(directory, client, tmp_path)
    with pytest.raises(SimplifiError):
        resolve_category([CATEGORY, {**CATEGORY, "id": "duplicate"}], name="Restaurants")


def test_confirm_dismiss_unresolved_do_not_write(setup, tmp_path, monkeypatch):
    directory, client, _ = setup
    decide(directory, action="confirm", category_id=None, transaction_ids=["t1"], note="Repair")
    record_decisions(
        directory,
        [{"id": "d2", "finding_id": "f1", "transaction_ids": ["t2"], "action": "dismiss"}],
    )
    path, kwargs = prepare(directory, client, tmp_path)
    writer = Mock()
    monkeypatch.setattr("simplifi.transaction_plans.update_category", writer)
    execute_transaction_plan(client, path, **kwargs)
    writer.assert_not_called()
    assert review_summary(load_review(directory))["confirmed"] == 1


def test_render_escape_duplicates_and_failure(setup, tmp_path, monkeypatch):
    directory, _, _ = setup
    output = tmp_path / "report.html"
    render_review(directory, output)
    text = output.read_text()
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert text.count("<td>Example</td>") == 2
    monkeypatch.setattr("simplifi.reviews.os.replace", Mock(side_effect=OSError))
    with pytest.raises(OSError):
        render_review(directory, output)
    assert output.read_text() == text


def test_lock_and_corrupt_journal(setup, tmp_path, monkeypatch):
    directory, client, store = setup
    decide(directory)
    path, kwargs = prepare(directory, client, tmp_path)
    with JournalStore(kwargs["journal_dir"]).locked(), pytest.raises(SimplifiError):
        execute_transaction_plan(client, path, **kwargs)
    calls = []
    monkeypatch.setattr("simplifi.transaction_plans.update_category", fake_writer(store, calls))
    execute_transaction_plan(client, path, **kwargs)
    checkpoint = kwargs["journal_dir"] / "checkpoint-00000001.dpapi"
    checkpoint.unlink()
    with pytest.raises(SimplifiError):
        transaction_plan_status(kwargs["journal_dir"])


def test_candidates_exact_ambiguous_excluded_and_offsets(setup):
    directory, _, _ = setup
    snapshot = load_review(directory).current
    assert reimbursement_candidates(snapshot, "t1")["candidates"][0]["transaction_id"] == "t2"
    assert not reimbursement_candidates(snapshot, "t1", used_expense_ids=["t2"])["candidates"]
    snapshot = snapshot.model_copy(
        update={"transactions": snapshot.transactions + [txn("t3", -20)]}
    )
    assert reimbursement_candidates(snapshot, "t1")["ambiguous"]
    decide(directory, transaction_ids=["t1"], offsets={"t1": "t2"})
    assert load_review(directory).context["reimbursement_offsets"][0]["amount_difference"] == "0"


def test_analysis_keeps_numbers_and_excludes_investments(setup):
    directory, _, _ = setup
    review = load_review(directory)
    values = [
        txn(),
        txn("t2", -20, {"type": "CATEGORY", "id": "dining"}),
        {**txn("investment"), "type": "INVESTMENT"},
    ]
    snapshot = review.current.model_copy(update={"transactions": values})
    findings = analyze(snapshot, existing=review.findings)
    assert findings[0].number == 57 and findings[1].number == 58
    assert len(analyze(snapshot, existing=findings)) == len(findings)
    assert "investment" not in findings[1].transaction_ids


def test_legacy_preview_import_and_missing_receipts(tmp_path):
    source = tmp_path / "legacy"
    original = {
        "dataset_id": "dataset",
        "downloaded_at": "2026-01-11",
        "transactions": [txn(), txn("t2", -20)],
        "categories": [CATEGORY],
    }
    current = copy.deepcopy(original)
    current["transactions"][0]["coa"] = {"type": "CATEGORY", "id": "dining"}
    save_encrypted(source / "snapshot.dpapi", original)
    save_encrypted(source / "snapshot-current.dpapi", current)
    save_encrypted(
        source / "findings.dpapi",
        {"findings": [{"number": 57, "title": "Example", "transaction_ids": ["t1", "t2"]}]},
    )
    save_encrypted(source / "0-verified.dpapi", {"transaction": current["transactions"][0]})
    before = set(source.rglob("*"))
    review, preview = build_legacy_import(source)
    assert set(source.rglob("*")) == before
    assert review.findings[0].number == 57 and len(review.decisions) == 1
    assert preview["blocked"] == 1
    import_legacy_review(source, tmp_path / "imported", confirmation=preview["import_digest"])
    assert load_review(tmp_path / "imported").id == review.id
    with pytest.raises(SimplifiError):
        import_legacy_review(source, tmp_path / "bad", confirmation="wrong")
    assert load_encrypted(source / "snapshot.dpapi") == original


@pytest.mark.parametrize(
    "change",
    [
        {"type": "INVESTMENT"},
        {"split": [{"amount": 20}]},
        {"transfer": {"id": "counterpart"}},
    ],
)
def test_unsupported_records_rejected_before_dispatch(setup, tmp_path, change):
    directory, client, store = setup
    decide(directory)
    store["t2"].update(change)
    with pytest.raises(ReadOnlyViolation):
        prepare(directory, client, tmp_path)


def test_failure_before_dispatch_checkpoint_can_resume(setup, tmp_path, monkeypatch):
    from simplifi.transaction_plans import TransactionJournal

    directory, client, store = setup
    decide(directory)
    path, kwargs = prepare(directory, client, tmp_path)
    checkpoint = TransactionJournal.checkpoint
    calls = []
    monkeypatch.setattr("simplifi.transaction_plans.update_category", fake_writer(store, calls))
    monkeypatch.setattr(TransactionJournal, "checkpoint", Mock(side_effect=OSError))
    with pytest.raises(OSError):
        execute_transaction_plan(client, path, **kwargs)
    assert calls == []
    monkeypatch.setattr(TransactionJournal, "checkpoint", checkpoint)
    execute_transaction_plan(client, path, **kwargs)
    assert calls == ["t1", "t2"]


def test_sign_flip_readback_stops_remaining_writes(setup, tmp_path, monkeypatch):
    directory, client, store = setup
    decide(directory)
    path, kwargs = prepare(directory, client, tmp_path)
    calls = []

    def bad_writer(client, original, category, *, backup_path):
        calls.append(original["id"])
        store[original["id"]].update(amount=-20, coa={"type": "CATEGORY", "id": "dining"})

    monkeypatch.setattr("simplifi.transaction_plans.update_category", bad_writer)
    with pytest.raises(SimplifiError):
        execute_transaction_plan(client, path, **kwargs)
    assert calls == ["t1"]


def test_atomic_projection_failure_preserves_old_review(setup, monkeypatch):
    directory, _, _ = setup
    before = (directory / "review.dpapi").read_bytes()
    monkeypatch.setattr("simplifi.reviews.os.replace", Mock(side_effect=OSError))
    with pytest.raises(OSError):
        decide(directory)
    assert (directory / "review.dpapi").read_bytes() == before
    assert not list(directory.glob(".*.dpapi"))


def test_cli_local_commands_never_construct_client(setup, monkeypatch, capsys):
    from io import StringIO

    from simplifi.review_cli import main

    directory, _, _ = setup
    forbidden = Mock(side_effect=AssertionError("Must stay offline"))
    monkeypatch.setattr("simplifi.review_cli.Client", forbidden)
    main(["summary", "--review", str(directory)])
    monkeypatch.setattr(
        "sys.stdin",
        StringIO(
            '[{"id":"d1","finding_id":"f1",'
            '"transaction_ids":["t1"],"action":"confirm","note":"Example"}]'
        ),
    )
    main(["decide", "--review", str(directory), "--stdin"])
    main(["render", "--review", str(directory), "--output", str(directory / "report.html")])
    forbidden.assert_not_called()
    assert "confirmed" in capsys.readouterr().out


def test_atomic_checkpoint_publish_is_exclusive(tmp_path):
    from simplifi.journal_store import EncryptedJsonStorage

    store = EncryptedJsonStorage()
    path = tmp_path / "checkpoint-00000000.dpapi"
    store.write(path, {"value": "original"})
    with pytest.raises(FileExistsError):
        store.write(path, {"value": "replacement"})
    assert store.read(path) == {"value": "original"}
    assert not list(tmp_path.glob(".*.dpapi"))


def test_identical_visible_rows_keep_distinct_decisions(setup, tmp_path):
    directory, _, _ = setup
    old = load_review(directory)
    snapshot = old.original.model_copy(update={"transactions": [txn("t1"), txn("t2")]})
    review = Review(id="identical", original=snapshot, current=snapshot, findings=old.findings)
    target = tmp_path / "identical"
    create_review(target, review)
    record_decisions(
        target,
        [
            {
                "id": "d1",
                "finding_id": "f1",
                "transaction_ids": ["t1"],
                "action": "confirm",
                "note": "First only",
            }
        ],
    )
    render_review(target, target / "report.html")
    text = (target / "report.html").read_text()
    assert text.count("<td>Example</td>") == 2
    assert text.count("<td>First only</td>") == 1
    assert "<td>unresolved</td>" in text


def test_stale_confirmation_does_not_confirm_new_category(setup, tmp_path):
    directory, client, store = setup
    decide(directory, action="confirm", category_id=None, transaction_ids=["t1"])
    store["t1"]["coa"] = {"type": "CATEGORY", "id": "dining"}
    with pytest.raises(SimplifiError, match="Confirmation changed"):
        prepare(directory, client, tmp_path)


def test_preflight_catches_changes_during_category_read(setup, tmp_path, monkeypatch):
    directory, client, store = setup
    decide(directory)
    path, kwargs = prepare(directory, client, tmp_path)
    categories = client.categories.return_value

    def categories_with_concurrent_edit():
        store["t2"]["amount"] = -99
        return categories

    client.categories.side_effect = categories_with_concurrent_edit
    writer = Mock()
    monkeypatch.setattr("simplifi.transaction_plans.update_category", writer)
    with pytest.raises(SimplifiError, match="Selected transaction changed"):
        execute_transaction_plan(client, path, **kwargs)
    writer.assert_not_called()
