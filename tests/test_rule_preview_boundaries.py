"""Signed exact-decimal boundaries and read-only zero-threshold diagnostics."""

import copy
from decimal import Decimal, InvalidOperation
from unittest.mock import Mock

import pytest

from simplifi.errors import SimplifiError
from simplifi.rule_preview import (
    amount_matches,
    inspect_amount_probe,
    preview_warnings,
    probe_rule_amounts,
)
from simplifi.transaction_rules import PREVIEW_PATH, digest, load_encrypted


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(
        "requests.sessions.Session.request", Mock(side_effect=AssertionError("Offline tests"))
    )


@pytest.mark.parametrize(
    "operator,boundary,value,expected",
    [
        ("GREATER_THAN", "0", "0", False),
        ("GREATER_THAN", "0", "0.01", True),
        ("GREATER_THAN", "0.01", "0.01", False),
        ("GREATER_THAN", "0.01", "0.02", True),
        ("LESS_THAN", "0", "-0.01", True),
        ("LESS_THAN", "0", "0", False),
        ("EQUALS", "0.10", "0.1", True),
        ("EQUALS", "0.10", "0.1000000001", False),
        ("GREATER_THAN", "-0.01", "0", True),
        ("LESS_THAN", "-0.01", "-0.02", True),
    ],
)
def test_signed_exact_boundaries(operator, boundary, value, expected):
    assert (
        amount_matches({"amountMatchingOperator": operator, "amount1": boundary}, value) is expected
    )


def test_range_and_any_amount_local_model():
    condition = {"amountMatchingOperator": "BETWEEN", "amount1": "-0.01", "amount2": "0.01"}
    assert amount_matches(condition, Decimal("-0.01"))
    assert amount_matches(condition, Decimal("0.01"))
    assert not amount_matches(condition, Decimal("0.02"))
    assert amount_matches({"amountMatchingOperator": "ANY_AMOUNT"}, 0)
    with pytest.raises(ValueError):
        amount_matches({**condition, "amount1": 2}, 0)


@pytest.mark.parametrize("value", [True, None, "NaN", "Infinity", "invalid"])
def test_invalid_amounts_are_not_matches(value):
    with pytest.raises((ValueError, InvalidOperation)):
        amount_matches({"amountMatchingOperator": "GREATER_THAN", "amount1": 0}, value)


def test_warnings_preserve_zero_and_disclose_one_cent_exclusion():
    zero = {"amountSourceCondition": {"amountMatchingOperator": "GREATER_THAN", "amount1": 0}}
    before = copy.deepcopy(zero)
    warnings = preview_warnings(zero)
    assert any(w["code"] == "zero_threshold_unverified" for w in warnings)
    assert zero == before
    penny = {"amountSourceCondition": {"amountMatchingOperator": "GREATER_THAN", "amount1": 0.01}}
    assert preview_warnings(penny)[0]["boundary"] == "0.01"


@pytest.fixture
def setup(monkeypatch):
    original = {
        "id": "rule",
        "payeeSourceCondition": {
            "payeeType": "STATEMENT",
            "payeeMatchingCriteria": [
                {"payeeMatchingOperator": "CONTAINS", "payeeName": "SYNTHETIC"}
            ],
        },
        "accountSourceCondition": {
            "accountSourceType": "SPECIFIC",
            "accountSourceIds": ["account"],
        },
        "amountSourceCondition": {"amountMatchingOperator": "GREATER_THAN", "amount1": 0},
        "coaTargetCondition": {"type": "CATEGORY", "id": "category"},
    }
    examples = {
        tid: {
            "id": tid,
            "accountId": "account",
            "amount": amount,
            "cpData": {"payee": "SYNTHETIC PAYOUT"},
        }
        for tid, amount in [
            ("negative", -0.01),
            ("zero", 0),
            ("penny", 0.01),
            ("two-pennies", 0.02),
        ]
    }
    client = Mock(dataset_id="dataset")
    client.transaction_rule.side_effect = lambda tid: copy.deepcopy(original)
    client.transaction.side_effect = lambda tid: copy.deepcopy(examples[tid])
    calls = []

    def preview(client, method, path, payload):
        calls.append((method, path, copy.deepcopy(payload)))
        # Reproduce the observed zero anomaly without asserting a server-side cause.
        threshold = payload["amountSourceCondition"]["amount1"]
        rows = [] if threshold == 0 else [examples["two-pennies"]]
        return {"transactions": rows, "srcTxnsCount": len(rows), "resultTxnsCount": len(rows)}

    monkeypatch.setattr("simplifi.transaction_rules._request", preview)
    return client, original, examples, calls


def test_probe_only_uses_read_only_preview_and_keeps_exact_payloads(setup, tmp_path):
    client, original, examples, calls = setup
    before = copy.deepcopy(original)
    path = tmp_path / "probe.dpapi"
    result = probe_rule_amounts(client, "rule", list(examples), output_path=path)
    assert result["live_changes"] is False and result["suspected_discrepancies"] == 2
    assert result["server_cause"] == "unconfirmed" and not result["sample_exhaustive"]
    assert len(calls) == 2 and all(
        method == "POST" and endpoint == PREVIEW_PATH for method, endpoint, _ in calls
    )
    assert [c[2]["amountSourceCondition"]["amount1"] for c in calls] == [0, 0.01]
    assert all(c[2]["applyToExistingTransactions"] is False for c in calls)
    report = inspect_amount_probe(path)
    assert report == load_encrypted(path) and original == before
    assert b"SYNTHETIC PAYOUT" not in path.read_bytes()
    one_cent = report["probes"][1]
    assert (
        next(c for c in one_cent["checks"] if c["transaction_id"] == "penny")["local_expected"]
        is False
    )


def test_unknown_nonamount_conditions_do_not_claim_a_match(setup, tmp_path):
    client, original, examples, _ = setup
    original["usageSourceType"] = "PERSONAL"
    path = tmp_path / "unknown.dpapi"
    probe_rule_amounts(client, "rule", list(examples), output_path=path)
    assert all(
        c["local_expected"] is None
        for p in inspect_amount_probe(path)["probes"]
        for c in p["checks"]
    )


def test_probe_detects_changed_evidence_without_saving_success(setup, tmp_path):
    client, original, examples, _ = setup
    client.transaction_rule.side_effect = [
        copy.deepcopy(original),
        {**original, "modifiedAt": "after"},
    ]
    path = tmp_path / "changed.dpapi"
    with pytest.raises(SimplifiError):
        probe_rule_amounts(client, "rule", list(examples), output_path=path)
    assert not path.exists()


@pytest.mark.parametrize(
    "thresholds",
    [["NaN"], ["0", "0.0"], [], [str(Decimal("0.1") + Decimal("1e-20"))]],
)
def test_invalid_probe_thresholds_fail_before_api_reads(setup, tmp_path, thresholds):
    client, _, examples, calls = setup
    with pytest.raises(SimplifiError):
        probe_rule_amounts(
            client,
            "rule",
            list(examples),
            output_path=tmp_path / "bad.dpapi",
            thresholds=thresholds,
        )
    assert not calls and client.transaction_rule.call_count == 0


def test_probe_report_command_is_offline(setup, tmp_path, monkeypatch):
    from simplifi.rules_cli import main

    client, _, examples, _ = setup
    path = tmp_path / "probe.dpapi"
    probe_rule_amounts(client, "rule", list(examples), output_path=path)
    forbidden = Mock(side_effect=AssertionError("Local report command"))
    monkeypatch.setattr("simplifi.rules_cli.Client", forbidden)
    main(["amount-report", "--report", str(path)])
    forbidden.assert_not_called()


def test_preview_warnings_do_not_change_rule_plan_digest(tmp_path, monkeypatch):
    from simplifi.rule_plans import inspect_change_plan, prepare_change_plan

    original = {
        "id": "rule",
        "amountSourceCondition": {"amountMatchingOperator": "GREATER_THAN", "amount1": 0},
        "coaTargetCondition": {"type": "CATEGORY", "id": "old"},
    }
    client = Mock(dataset_id="dataset")
    client.transaction_rules.return_value = [original]
    monkeypatch.setattr("simplifi.rule_plans.preview_rule", Mock(return_value={"transactions": []}))
    path = tmp_path / "plan.dpapi"
    prepare_change_plan(
        client,
        [
            {
                "action": "update",
                "rule_id": "rule",
                "changes": {"coaTargetCondition": {"type": "CATEGORY", "id": "new"}},
            }
        ],
        plan_path=path,
    )
    before = path.read_bytes()
    preview = inspect_change_plan(path)
    assert preview["confirmation"] == digest(load_encrypted(path))
    assert preview["operations"][0]["preview"]["warnings"]
    assert path.read_bytes() == before


def test_zero_anomaly_warning_is_scoped_to_observed_operator():
    from simplifi.rule_preview import preview_warnings

    for operator in ("EQUALS", "LESS_THAN"):
        warnings = preview_warnings(
            {"amountSourceCondition": {"amountMatchingOperator": operator, "amount1": 0}}
        )
        assert all(w["code"] != "zero_threshold_unverified" for w in warnings)


def test_probe_existing_output_fails_before_network(setup, tmp_path):
    client, _, examples, calls = setup
    path = tmp_path / "existing.dpapi"
    path.write_bytes(b"existing encrypted report placeholder")
    with pytest.raises(SimplifiError, match="already exists"):
        probe_rule_amounts(client, "rule", list(examples), output_path=path)
    assert path.read_bytes() == b"existing encrypted report placeholder"
    client.transaction_rule.assert_not_called()
    assert not calls


def test_probe_rejects_wrong_rule_identity_before_preview(setup, tmp_path):
    client, original, examples, calls = setup
    original["id"] = "another-rule"
    path = tmp_path / "wrong-rule.dpapi"
    with pytest.raises(SimplifiError, match="different ID"):
        probe_rule_amounts(client, "rule", list(examples), output_path=path)
    client.transaction.assert_not_called()
    assert not calls and not path.exists()
