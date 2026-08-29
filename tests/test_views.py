from __future__ import annotations

from simplifi.views import (
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
        t["id"]: classify_for_spending(t, as_of="2026-08-29", known_ids=known)
        for t in TRANSACTIONS
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
