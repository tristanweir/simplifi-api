from __future__ import annotations

from unittest.mock import Mock

import pytest

from simplifi.errors import AuthError, SimplifiError
from simplifi.views import (
    Views,
    bills_income_upcoming,
    cash_flow_projection,
    dashboard_snapshot,
    income_vs_expense,
    monthly_burn,
    monthly_summary,
    spending_by_category,
    spending_plan_summary,
)
from simplifi.views.classify import classify_for_spending, transaction_amount
from tests.fixtures.synthetic import ACCOUNTS, CATEGORIES, GOALS, SCHEDULED, TRANSACTIONS


def test_classify_excludes_transfers_investments_projections():
    known = {str(t["id"]) for t in TRANSACTIONS}
    kinds = {
        t["id"]: classify_for_spending(t, as_of="2026-08-29", known_ids=known) for t in TRANSACTIONS
    }
    assert kinds["t1"] == "spending"
    assert kinds["t3"] == "income"
    assert kinds["t4"] == "transfer"
    assert kinds["t5"] == "projected"
    assert kinds["t6"] == "investment"
    assert kinds["t7"] == "balance-adjustment"


def test_spending_by_category():
    names = {c["id"]: c["name"] for c in CATEGORIES}
    report = spending_by_category(
        TRANSACTIONS,
        from_date="2026-08-01",
        to_date="2026-08-31",
        as_of="2026-08-29",
        category_names=names,
    )
    food = next(c for c in report.categories if c.category_name == "Food")
    assert food.total == -187.5
    assert food.transaction_count == 3
    assert report.spending_total == -187.5
    kinds = {e.kind for e in report.exclusions}
    assert "transfer" in kinds
    assert "projected" in kinds
    assert "investment" in kinds


def test_monthly_burn_marks_incomplete_current_month():
    report = monthly_burn(
        TRANSACTIONS,
        from_date="2026-07-01",
        to_date="2026-08-31",
        as_of="2026-08-29",
    )
    by_month = {row.month: row for row in report.months}
    assert by_month["2026-07"].complete is True
    assert by_month["2026-08"].complete is False
    assert by_month["2026-07"].money_out == -80.0
    assert report.average_net_complete_months == by_month["2026-07"].net


def test_income_vs_expense():
    report = income_vs_expense(
        TRANSACTIONS,
        from_date="2026-08-01",
        to_date="2026-08-31",
        as_of="2026-08-29",
    )
    assert report.income == 3000.0
    assert report.expense == -187.5
    assert report.net == 3000.0 - 187.5


def test_monthly_summary_top_payees():
    names = {c["id"]: c["name"] for c in CATEGORIES}
    summary = monthly_summary(
        TRANSACTIONS,
        month="2026-08",
        as_of="2026-08-29",
        category_names=names,
    )
    assert summary.month == "2026-08"
    assert summary.top_payees[0]["payee"] in {"Market", "Cafe Example"}


def test_spending_plan_summary():
    plan = spending_plan_summary(
        month="2026-08",
        transactions=TRANSACTIONS,
        scheduled=SCHEDULED,
        goals=GOALS,
        as_of="2026-08-29",
    )
    assert plan.bills == 1500.0
    assert plan.subscriptions == 15.0
    assert plan.goals == 200.0
    assert plan.other_spend == 187.5
    assert plan.income == 3000.0
    assert plan.left_this_month == 3000.0 - 1500 - 15 - 200 - 187.5


def test_bills_income_upcoming_splits_past_due():
    result = bills_income_upcoming(
        SCHEDULED,
        from_date="2026-07-01",
        to_date="2026-08-31",
        as_of="2026-08-29",
    )
    assert any(r.payee == "Past Due Utility" for r in result.past_due)
    assert any(r.payee == "Rent" for r in result.reminders + result.past_due)


def test_cash_flow_projection():
    proj = cash_flow_projection(
        SCHEDULED,
        starting_balance=1000,
        from_date="2026-08-01",
        to_date="2026-08-31",
    )
    assert proj.starting_balance == 1000
    assert proj.ending_balance != 1000
    assert len(proj.points) >= 2


def test_dashboard_snapshot():
    snap = dashboard_snapshot(
        accounts=ACCOUNTS,
        scheduled=SCHEDULED,
        transactions=TRANSACTIONS,
        as_of="2026-08-29",
    )
    assert snap.account_count == 3
    assert snap.total_assets == 15000
    assert snap.total_liabilities == 200
    assert snap.net_worth == 14800
    assert snap.recent_transaction_count >= 1


def test_transaction_amount_cents():
    assert transaction_amount({"amountCents": -4250}) == -42.5


@pytest.mark.parametrize("subscription", [False, True])
def test_paid_recurring_transaction_is_not_other_spending(subscription):
    flag = "isSubscription" if subscription else "isBill"
    transactions = [
        {"id": "income", "amount": 1000, "postedOn": "2026-01-10"},
        {"id": "paid", "amount": -100, "postedOn": "2026-01-10", flag: True},
        {"id": "other", "amount": -25, "postedOn": "2026-01-11"},
    ]
    scheduled = [
        {
            "id": "recurring",
            "type": "SUBSCRIPTION" if subscription else "BILL",
            "amount": 100,
            "dueOn": "2026-01-10",
        }
    ]
    result = spending_plan_summary(
        month="2026-01",
        transactions=transactions,
        scheduled=scheduled,
        goals=[],
        as_of="2026-01-15",
    )
    assert result.other_spend == 25
    assert result.bills + result.subscriptions == 100
    assert result.left_this_month == 875


def test_linked_subscription_does_not_enter_bill_bucket():
    result = spending_plan_summary(
        month="2026-01",
        transactions=[
            {"amount": 1000, "postedOn": "2026-01-10"},
            {"amount": -100, "postedOn": "2026-01-10", "stModelId": "subscription"},
        ],
        scheduled=[
            {"id": "subscription", "type": "SUBSCRIPTION", "amount": 100, "dueOn": "2026-01-10"}
        ],
        goals=[],
        as_of="2026-01-15",
    )
    assert result.bills == 0 and result.subscriptions == 100
    assert result.other_spend == 0 and result.left_this_month == 900


def test_actual_bill_is_counted_when_schedule_is_missing():
    result = spending_plan_summary(
        month="2026-01",
        transactions=[
            {"amount": 1000, "postedOn": "2026-01-10"},
            {"amount": -100, "postedOn": "2026-01-10", "isBill": True},
        ],
        scheduled=[],
        goals=[],
        as_of="2026-01-15",
    )
    assert result.bills == 100 and result.other_spend == 0
    assert result.left_this_month == 900


def test_over_budget_paid_bill_does_not_hide_another_unpaid_bill():
    result = spending_plan_summary(
        month="2026-01",
        transactions=[
            {"amount": 1000, "postedOn": "2026-01-10"},
            {"amount": -150, "postedOn": "2026-01-10", "stModelId": "paid"},
        ],
        scheduled=[
            {"id": "paid", "type": "BILL", "amount": 100, "dueOn": "2026-01-10"},
            {"id": "unpaid", "type": "BILL", "amount": 200, "dueOn": "2026-01-25"},
        ],
        goals=[],
        as_of="2026-01-15",
    )
    assert result.bills == 350
    assert result.other_spend == 0 and result.left_this_month == 650


@pytest.mark.parametrize(
    "method,kwargs",
    [
        (
            "spending_by_category",
            {
                "transactions": [],
                "categories": [],
                "from_date": "2026-01-01",
                "to_date": "2026-01-31",
            },
        ),
        ("monthly_burn", {"transactions": [], "from_date": "2026-01-01", "to_date": "2026-01-31"}),
        (
            "income_vs_expense",
            {"transactions": [], "from_date": "2026-01-01", "to_date": "2026-01-31"},
        ),
        ("monthly_summary", {"transactions": [], "category_names": {}, "month": "2026-01"}),
        (
            "spending_plan_summary",
            {"transactions": [], "scheduled": [], "goals": [], "month": "2026-01"},
        ),
        (
            "bills_income_upcoming",
            {"scheduled": [], "from_date": "2026-01-01", "to_date": "2026-01-31"},
        ),
        (
            "cash_flow_projection",
            {
                "scheduled": [],
                "starting_balance": 0,
                "from_date": "2026-01-01",
                "to_date": "2026-01-31",
            },
        ),
        ("dashboard_snapshot", {"accounts": [], "scheduled": [], "transactions": []}),
    ],
)
def test_explicit_empty_inputs_never_fetch(method, kwargs):
    client = Mock()
    if method != "cash_flow_projection":
        kwargs = {**kwargs, "as_of": "2026-01-15"}
    getattr(Views(client), method)(**kwargs)
    assert not client.mock_calls


def test_omitted_transactions_still_fetch():
    client = Mock()
    client.transactions.return_value = []
    Views(client).monthly_burn(from_date="2026-01-01", to_date="2026-01-31")
    client.transactions.assert_called_once_with()


@pytest.mark.parametrize(
    "error",
    [
        AuthError("synthetic auth failure"),
        SimplifiError("synthetic network failure"),
        RuntimeError("synthetic bug"),
    ],
)
def test_goal_failures_propagate(error):
    client = Mock()
    client.goals.side_effect = error
    with pytest.raises(type(error)):
        Views(client).spending_plan_summary("2026-01", transactions=[], scheduled=[])
