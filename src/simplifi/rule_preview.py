"""Exact local amount boundaries and read-only probes of server preview coverage."""

from decimal import Decimal, InvalidOperation
from pathlib import Path

from simplifi.errors import SimplifiError
from simplifi.transaction_rules import load_encrypted, preview_rule, rule_payload, save_encrypted


def decimal_amount(value):
    if value is None or isinstance(value, bool):
        raise ValueError("Amount must be numeric")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Amount must be finite")
    return result


def amount_matches(condition, value):
    """Local signed-decimal model, not an assertion about undocumented server logic."""
    value = decimal_amount(value)
    operator = condition.get("amountMatchingOperator")
    if operator == "ANY_AMOUNT":
        return True
    boundary = decimal_amount(condition.get("amount1"))
    if operator == "EQUALS":
        return value == boundary
    if operator == "GREATER_THAN":
        return value > boundary
    if operator == "LESS_THAN":
        return value < boundary
    if operator == "BETWEEN":
        upper = decimal_amount(condition.get("amount2"))
        if upper < boundary:
            raise ValueError("Invalid amount range")
        return boundary <= value <= upper
    raise ValueError("Unknown amount operator")


def preview_warnings(payload):
    condition = payload.get("amountSourceCondition") or {}
    operator = condition.get("amountMatchingOperator")
    if not condition or operator == "ANY_AMOUNT":
        return []
    boundary = decimal_amount(condition.get("amount1"))
    warnings = []
    if operator == "GREATER_THAN" and boundary == 0:
        warnings.append(
            {
                "code": "zero_threshold_unverified",
                "message": "Observed GREATER_THAN 0 previews were inconsistent; verify boundary examples.",
                "server_cause": "unconfirmed",
            }
        )
    if operator in {"GREATER_THAN", "LESS_THAN"}:
        warnings.append(
            {
                "code": "strict_boundary",
                "boundary": str(boundary),
                "message": "Strict comparison excludes the boundary amount. Changing zero to one cent changes coverage.",
            }
        )
    return warnings


def _other_conditions(payload, transaction):
    """Return None for unsupported semantics; never treat them as a match."""
    unknown = bool(payload.get("usageSourceType") or payload.get("coaSourceConditions"))
    payee = payload.get("payeeSourceCondition")
    if payee:
        cp = transaction.get("cpData") or {}
        value = (
            cp.get("payee")
            if payee.get("payeeType") == "STATEMENT"
            else cp.get("inferredPayee")
            if payee.get("payeeType") == "INFERRED"
            else None
        )
        if value is None:
            unknown = True
        else:
            checks = []
            for criterion in payee.get("payeeMatchingCriteria", []):
                pattern = str(criterion.get("payeeName", "")).casefold()
                operator = criterion.get("payeeMatchingOperator")
                if operator not in {"EQUALS", "CONTAINS"}:
                    unknown = True
                    continue
                checks.append(
                    str(value).casefold() == pattern
                    if operator == "EQUALS"
                    else pattern in str(value).casefold()
                )
            if checks and not any(checks):
                return False
    source = payload.get("accountSourceCondition")
    if source:
        if source.get("accountSourceType") != "SPECIFIC":
            unknown = True
        elif str(transaction.get("accountId")) not in source.get("accountSourceIds", []):
            return False
    return None if unknown else True


def probe_rule_amounts(
    client,
    rule_id,
    transaction_ids,
    *,
    output_path,
    thresholds=("0", "0.01"),
    operator="GREATER_THAN",
):
    if Path(output_path).suffix != ".dpapi":
        raise SimplifiError("Probe reports must use encrypted .dpapi output")
    if Path(output_path).exists():
        raise SimplifiError("Probe output already exists; choose a new path")
    if operator not in {"GREATER_THAN", "LESS_THAN", "EQUALS"}:
        raise SimplifiError("Probe operator must use a single threshold")
    if (
        not client.dataset_id
        or not transaction_ids
        or len(set(transaction_ids)) != len(transaction_ids)
    ):
        raise SimplifiError("Select a dataset and unique boundary example transactions")
    try:
        boundaries = [decimal_amount(t) for t in thresholds]
        if not 1 <= len(boundaries) <= 20 or len(set(boundaries)) != len(boundaries):
            raise ValueError("Select distinct thresholds")
        numbers = [float(t) for t in boundaries]
        if any(decimal_amount(n) != t for n, t in zip(numbers, boundaries)):
            raise ValueError("Threshold loses precision in the JSON numeric contract")
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise SimplifiError("Invalid or imprecise probe thresholds") from None
    original = client.transaction_rule(rule_id)
    if str(original.get("id")) != rule_id:
        raise SimplifiError("Rule lookup returned a different ID")
    examples = {tid: client.transaction(tid) for tid in transaction_ids}
    if any(str(t.get("id")) != tid for tid, t in examples.items()):
        raise SimplifiError("Boundary example returned a different ID")
    try:
        for transaction in examples.values():
            decimal_amount(transaction.get("amount"))
    except (ValueError, TypeError, InvalidOperation):
        raise SimplifiError("Boundary examples require finite decimal amounts") from None
    probes = []
    for boundary, number in zip(boundaries, numbers):
        changes = {"amountSourceCondition": {"amountMatchingOperator": operator, "amount1": number}}
        payload = rule_payload(original, changes, apply_to_existing=False)
        response = preview_rule(client, original, changes, apply_to_existing=False)
        sample = {str(t.get("id")) for t in response["transactions"]}
        checks = []
        for tid, t in examples.items():
            other = _other_conditions(payload, t)
            local = amount_matches(payload["amountSourceCondition"], t.get("amount"))
            expected = False if other is False else local if other is True else None
            seen = tid in sample
            checks.append(
                {
                    "transaction_id": tid,
                    "amount": str(decimal_amount(t.get("amount"))),
                    "local_expected": expected,
                    "observed_in_sample": seen,
                    "needs_investigation": expected is not None and expected != seen,
                }
            )
        probes.append(
            {
                "threshold": str(boundary),
                "payload": payload,
                "preview": response,
                "checks": checks,
                "warnings": preview_warnings(payload),
                "sample_exhaustive": False,
            }
        )
    if client.transaction_rule(rule_id) != original or any(
        client.transaction(tid) != t for tid, t in examples.items()
    ):
        raise SimplifiError("Rule or boundary examples changed during probe")
    report = {
        "version": 1,
        "kind": "rule-amount-probe",
        "dataset_id": client.dataset_id,
        "original": original,
        "examples": examples,
        "probes": probes,
        "server_cause": "unconfirmed",
        "live_changes": False,
        "limitations": [
            "Sample absence does not prove nonmatching.",
            "Local predicates are not a verified server contract.",
        ],
    }
    save_encrypted(Path(output_path), report)
    return {
        "saved": True,
        "probe_count": len(probes),
        "live_changes": False,
        "suspected_discrepancies": sum(
            c["needs_investigation"] for p in probes for c in p["checks"]
        ),
        "server_cause": "unconfirmed",
        "sample_exhaustive": False,
    }


def inspect_amount_probe(path):
    value = load_encrypted(Path(path))
    if value.get("kind") != "rule-amount-probe" or value.get("version") != 1:
        raise SimplifiError("Invalid amount probe report")
    return value
