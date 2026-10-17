import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import responses

from simplifi import rule_plans as plans
from simplifi.errors import ReadOnlyViolation, SimplifiError
from simplifi.transaction_rules import RuleRequestSession, digest, load_encrypted, save_encrypted


def rule(rid):
    return {
        "id": rid,
        "dbVersion": 1,
        "memoTarget": "private original",
        "payeeSourceCondition": {
            "payeeType": "STATEMENT",
            "payeeMatchingCriteria": [
                {"payeeMatchingOperator": "CONTAINS", "payeeName": "example merchant"}
            ],
        },
        "coaTargetCondition": {"type": "CATEGORY", "id": "category-a"},
        "applyToExistingTransactions": True,
    }


@pytest.fixture
def service(monkeypatch):
    # Block every real request, even if a refactor accidentally bypasses our mocks.
    with responses.RequestsMock(assert_all_requests_are_fired=False):
        remote = {rid: rule(rid) for rid in ("one", "two", "untouched")}
        client = SimpleNamespace(
            dataset_id="dataset",
            transaction_rules=Mock(side_effect=lambda: copy.deepcopy(list(remote.values()))),
        )
        preview = {"transactions": [], "resultTxnsCount": 0, "srcTxnsCount": 10}
        preview_mock = Mock(side_effect=lambda *a, **k: copy.deepcopy(preview))
        writes = []

        def request(client, method, path, payload):
            rid = path.rsplit("/", 1)[1]
            writes.append((method, rid))
            if method == "DELETE":
                assert payload is None
                del remote[rid]
            else:
                assert method == "PUT" and payload["applyToExistingTransactions"] is False
                remote[rid] = {**copy.deepcopy(payload), "dbVersion": 2}

        monkeypatch.setattr(plans, "preview_rule", preview_mock)
        monkeypatch.setattr(plans, "_request", request)
        yield SimpleNamespace(
            client=client,
            remote=remote,
            preview=preview,
            preview_mock=preview_mock,
            writes=writes,
            request=request,
        )


def prepare(service, tmp_path, operations=None):
    operations = operations or [
        {"action": "update", "rule_id": "one", "changes": {"memoTarget": "private revised"}},
        {"action": "delete", "rule_id": "two"},
    ]
    path = tmp_path / "plan.dpapi"
    summary = plans.prepare_change_plan(service.client, operations, plan_path=path)
    return path, summary["confirmation"], tmp_path / "journal"


def execute(service, prepared):
    path, confirmation, journal = prepared
    return plans.execute_change_plan(
        service.client, path, confirmation=confirmation, journal_dir=journal
    )


def test_mixed_batch_encrypted_backup_and_repeat_execution(service, tmp_path):
    prepared = prepare(service, tmp_path)
    assert not service.writes
    assert b"example merchant" not in prepared[0].read_bytes()
    assert execute(service, prepared)["complete"]
    assert service.writes == [("PUT", "one"), ("DELETE", "two")]
    assert service.remote["one"]["memoTarget"] == "private revised"
    assert service.remote["untouched"] == rule("untouched")
    backup = load_encrypted(prepared[2] / "backup-00000000.dpapi")
    assert backup["operation"]["original"] == rule("one")
    for path in prepared[2].glob("*.dpapi"):
        assert b"private original" not in path.read_bytes()
    assert execute(service, prepared)["complete"]
    assert len(service.writes) == 2  # Completed writes are never repeated.


@pytest.mark.parametrize("failure", ["confirmation", "dataset"])
def test_reject_approval_before_network(service, tmp_path, failure):
    path, confirmation, journal = prepare(service, tmp_path)
    service.client.transaction_rules.reset_mock()
    if failure == "confirmation":
        confirmation = "wrong"
    else:
        service.client.dataset_id = "other-dataset"
    with pytest.raises(ReadOnlyViolation):
        execute(service, (path, confirmation, journal))
    service.client.transaction_rules.assert_not_called()
    assert not service.writes


@pytest.mark.parametrize("rid", ["one", "untouched"])
def test_concurrent_rule_changes_block_batch(service, tmp_path, rid):
    prepared = prepare(service, tmp_path)
    service.remote[rid]["dbVersion"] += 1
    with pytest.raises(SimplifiError, match="outside this plan"):
        execute(service, prepared)
    assert not service.writes


def test_stale_preview_prevents_dispatch(service, tmp_path):
    prepared = prepare(service, tmp_path)
    service.preview["transactions"] = [{"id": "new-match"}]
    with pytest.raises(SimplifiError, match="Preview changed"):
        execute(service, prepared)
    assert not service.writes
    assert plans.change_plan_status(prepared[2])["states"] == ["pending", "pending"]


@pytest.mark.parametrize("action", ["update", "delete"])
def test_lost_success_response_reconciles_without_replay(service, tmp_path, monkeypatch, action):
    operation = {"action": action, "rule_id": "one"}
    if action == "update":
        operation["changes"] = {"memoTarget": "private revised"}
    prepared = prepare(service, tmp_path, [operation, {"action": "delete", "rule_id": "two"}])
    failed = False

    def lost_response(*args):
        nonlocal failed
        service.request(*args)
        if not failed:
            failed = True
            raise SimplifiError("Synthetic lost response")

    monkeypatch.setattr(plans, "_request", lost_response)
    with pytest.raises(SimplifiError, match="lost response"):
        execute(service, prepared)
    assert plans.change_plan_status(prepared[2])["states"] == ["dispatched", "pending"]
    assert execute(service, prepared)["complete"]
    assert len(service.writes) == 2


def test_uncertain_request_not_committed_is_never_retried(service, tmp_path, monkeypatch):
    prepared = prepare(service, tmp_path)
    mutation = Mock(side_effect=SimplifiError("Synthetic timeout"))
    monkeypatch.setattr(plans, "_request", mutation)
    with pytest.raises(SimplifiError, match="timeout"):
        execute(service, prepared)
    with pytest.raises(SimplifiError, match="outcome unresolved"):
        execute(service, prepared)
    assert mutation.call_count == 1


def test_readback_conflict_stays_dispatched_and_blocks_next_step(service, tmp_path, monkeypatch):
    prepared = prepare(service, tmp_path)

    def unexpected(*args):
        service.request(*args)
        service.remote["one"]["memoTarget"] = "server conflict"

    monkeypatch.setattr(plans, "_request", unexpected)
    with pytest.raises(SimplifiError, match="outcome unresolved"):
        execute(service, prepared)
    assert service.writes == [("PUT", "one")]
    assert plans.change_plan_status(prepared[2])["states"] == ["dispatched", "pending"]


def test_crash_after_backup_before_dispatch_can_resume(service, tmp_path, monkeypatch):
    prepared = prepare(service, tmp_path)
    checkpoint = plans._checkpoint
    monkeypatch.setattr(plans, "_checkpoint", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        execute(service, prepared)
    assert not service.writes
    assert (prepared[2] / "backup-00000000.dpapi").exists()
    assert plans.change_plan_status(prepared[2])["states"] == ["pending", "pending"]
    monkeypatch.setattr(plans, "_checkpoint", checkpoint)
    assert execute(service, prepared)["complete"]


def test_backup_failure_prevents_dispatch(service, tmp_path, monkeypatch):
    prepared = prepare(service, tmp_path)
    original_save = plans.save_encrypted

    def fail_backup(path, value):
        if path.name.startswith("backup-"):
            raise OSError("disk full")
        original_save(path, value)

    monkeypatch.setattr(plans, "save_encrypted", fail_backup)
    with pytest.raises(OSError):
        execute(service, prepared)
    assert not service.writes


def test_checkpoint_gap_blocks_resume(service, tmp_path):
    prepared = prepare(service, tmp_path)
    execute(service, prepared)
    (prepared[2] / "checkpoint-00000001.dpapi").unlink()
    with pytest.raises(SimplifiError, match="gap"):
        execute(service, prepared)
    assert len(service.writes) == 2


def test_truncated_checkpoint_blocks_resume(service, tmp_path):
    prepared = prepare(service, tmp_path)
    execute(service, prepared)
    last = max(prepared[2].glob("checkpoint-*.dpapi"))
    last.write_bytes(b"truncated")
    with pytest.raises(SimplifiError):
        execute(service, prepared)
    assert len(service.writes) == 2


def test_journal_lock_blocks_concurrent_executor(service, tmp_path):
    prepared = prepare(service, tmp_path)
    with plans._lock(prepared[2]), pytest.raises(SimplifiError, match="lock"):
        execute(service, prepared)
    assert not service.writes


@pytest.mark.parametrize(
    "operations",
    [
        [{"action": "delete", "rule_id": "one"}, {"action": "delete", "rule_id": "one"}],
        [{"action": "delete", "rule_id": "one", "changes": {}}],
        [{"action": "create", "rule_id": "one"}],
        [{"action": "update", "rule_id": "one", "changes": {"isDeleted": True}}],
        [{"action": "update", "rule_id": "one", "changes": {}, "apply_existing": True}],
    ],
)
def test_invalid_batch_rejected_before_preview(service, tmp_path, operations):
    with pytest.raises(ReadOnlyViolation):
        prepare(service, tmp_path, operations)
    service.preview_mock.assert_not_called()
    assert not service.writes


def test_history_flag_rejected_even_with_recomputed_confirmation(service, tmp_path):
    prepared = prepare(service, tmp_path)
    plan = load_encrypted(prepared[0])
    plan["operations"][0]["payload"]["applyToExistingTransactions"] = True
    bad_path = tmp_path / "bad.dpapi"
    save_encrypted(bad_path, plan)
    with pytest.raises(ReadOnlyViolation, match="future"):
        execute(service, (bad_path, digest(plan), prepared[2]))
    assert not service.writes


def test_delete_convenience_uses_common_workflow(service, tmp_path):
    path = tmp_path / "delete.dpapi"
    summary = plans.prepare_rule_deletion(service.client, "one", plan_path=path)
    assert summary["operations"][0]["original"] == rule("one")
    assert plans.apply_rule_deletion(
        service.client, path, confirmation=summary["confirmation"], journal_dir=tmp_path / "journal"
    )["complete"]
    assert service.writes == [("DELETE", "one")]


@responses.activate
def test_scoped_delete_has_no_body_no_redirect_no_reuse():
    session = RuleRequestSession("DELETE", "/transaction-rules/rule-1", None)
    with pytest.raises(ReadOnlyViolation):
        session.request("DELETE", session.url, json={"unapproved": True})
    responses.delete(session.url, status=204)
    assert session.request("DELETE", session.url) is None
    assert responses.calls[0].request.body is None
    with pytest.raises(ReadOnlyViolation):
        session.request("DELETE", session.url)


def test_cli_review_and_status_do_not_create_client(service, tmp_path, monkeypatch, capsys):
    from simplifi import rules_cli

    prepared = prepare(service, tmp_path)
    constructor = Mock(side_effect=AssertionError("Must not authenticate"))
    monkeypatch.setattr(rules_cli, "Client", constructor)
    monkeypatch.setattr("sys.argv", ["rules", "review", "--plan", str(prepared[0])])
    rules_cli.main()
    assert json.loads(capsys.readouterr().out)["confirmation"] == prepared[1]
    monkeypatch.setattr("sys.argv", ["rules", "status", "--journal", str(prepared[2])])
    rules_cli.main()
    assert json.loads(capsys.readouterr().out) == {"started": False}
    constructor.assert_not_called()


def test_backup_and_dispatch_checkpoint_exist_before_request(service, tmp_path, monkeypatch):
    prepared = prepare(service, tmp_path)

    def checked_request(*args):
        index = len(service.writes)
        assert plans.change_plan_status(prepared[2])["states"][index] == "dispatched"
        backup = load_encrypted(prepared[2] / f"backup-{index:08d}.dpapi")
        assert backup["plan_digest"] == prepared[1]
        assert backup["operation"]["original"]["memoTarget"] == "private original"
        service.request(*args)

    monkeypatch.setattr(plans, "_request", checked_request)
    assert execute(service, prepared)["complete"]


def test_existing_journal_cannot_be_reused_for_another_plan(service, tmp_path):
    prepared = prepare(service, tmp_path)
    execute(service, prepared)
    other_path = tmp_path / "other.dpapi"
    other = plans.prepare_rule_deletion(service.client, "untouched", plan_path=other_path)
    with pytest.raises(ReadOnlyViolation, match="different plan"):
        plans.execute_change_plan(
            service.client, other_path, confirmation=other["confirmation"], journal_dir=prepared[2]
        )
    assert len(service.writes) == 2


def test_verified_rule_changed_later_blocks_resume(service, tmp_path):
    prepared = prepare(service, tmp_path)
    execute(service, prepared)
    service.remote["one"]["memoTarget"] = "concurrent edit"
    with pytest.raises(SimplifiError, match="outside this plan"):
        execute(service, prepared)
    assert len(service.writes) == 2


def test_missing_verified_backup_blocks_resume(service, tmp_path):
    prepared = prepare(service, tmp_path)
    execute(service, prepared)
    (prepared[2] / "backup-00000000.dpapi").unlink()
    with pytest.raises(SimplifiError, match="backup missing"):
        execute(service, prepared)
    assert len(service.writes) == 2


def test_single_delete_rejects_mixed_plan(service, tmp_path):
    prepared = prepare(service, tmp_path)
    with pytest.raises(ReadOnlyViolation, match="single-delete"):
        plans.apply_rule_deletion(
            service.client, prepared[0], confirmation=prepared[1], journal_dir=prepared[2]
        )
    assert not service.writes


@responses.activate
def test_delete_redirect_is_not_followed():
    session = RuleRequestSession("DELETE", "/transaction-rules/rule-1", None)
    responses.delete(session.url, status=307, headers={"Location": "https://example.invalid"})
    with pytest.raises(SimplifiError):
        session.request("DELETE", session.url)
    assert len(responses.calls) == 1


def test_cli_plan_and_execute_share_workflow(service, tmp_path, monkeypatch, capsys):
    from simplifi import rules_cli

    monkeypatch.setattr(rules_cli, "Client", Mock(return_value=service.client))
    path = tmp_path / "cli.dpapi"
    monkeypatch.setattr(
        "sys.argv", ["rules", "plan", "--delete-rule-id", "two", "--plan", str(path)]
    )
    rules_cli.main()
    summary = json.loads(capsys.readouterr().out)
    assert not service.writes
    monkeypatch.setattr(
        "sys.argv",
        [
            "rules",
            "delete",
            "--plan",
            str(path),
            "--confirm",
            summary["confirmation"],
            "--journal",
            str(tmp_path / "journal"),
        ],
    )
    rules_cli.main()
    assert json.loads(capsys.readouterr().out)["complete"]
    assert service.writes == [("DELETE", "two")]
