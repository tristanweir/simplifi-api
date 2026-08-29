"""Pydantic models for public response shapes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SimplifiModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class TokenSet(SimplifiModel):
    access_token: str
    refresh_token: str
    access_token_expires_at: str | None = None
    refresh_token_expires_at: str | None = None


class TracedFigure(SimplifiModel):
    total: float
    transaction_count: int
    transaction_ids: list[str] = Field(default_factory=list)


class CategoryTotal(SimplifiModel):
    category_id: str | None = None
    category_name: str
    total: float
    transaction_count: int
    transaction_ids: list[str] = Field(default_factory=list)


class ExclusionLine(SimplifiModel):
    kind: str
    reason: str
    count: int
    net: float
    transaction_ids: list[str] = Field(default_factory=list)


class SpendingByCategoryReport(SimplifiModel):
    from_date: str
    to_date: str
    as_of: str
    categories: list[CategoryTotal]
    exclusions: list[ExclusionLine]
    spending_total: float


class MonthlyBurnRow(SimplifiModel):
    month: str
    money_out: float
    money_in: float
    net: float
    complete: bool
    outflow_transaction_ids: list[str] = Field(default_factory=list)
    inflow_transaction_ids: list[str] = Field(default_factory=list)


class MonthlyBurnReport(SimplifiModel):
    from_date: str
    to_date: str
    as_of: str
    months: list[MonthlyBurnRow]
    average_net_complete_months: float | None = None
    exclusions: list[ExclusionLine]


class IncomeExpenseReport(SimplifiModel):
    from_date: str
    to_date: str
    as_of: str
    income: float
    expense: float
    net: float
    income_transaction_ids: list[str] = Field(default_factory=list)
    expense_transaction_ids: list[str] = Field(default_factory=list)
    exclusions: list[ExclusionLine]


class MonthlySummary(SimplifiModel):
    month: str
    income: float
    expense: float
    net: float
    top_categories: list[CategoryTotal]
    top_payees: list[dict[str, Any]]


class SpendingPlanSummary(SimplifiModel):
    month: str
    income: float
    bills: float
    subscriptions: float
    goals: float
    other_spend: float
    left_this_month: float
    scheduled_count: int
    transaction_count: int


class UpcomingReminder(SimplifiModel):
    id: str | None = None
    payee: str | None = None
    amount: float | None = None
    due_on: str | None = None
    type: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class BillsIncomeUpcoming(SimplifiModel):
    from_date: str
    to_date: str
    reminders: list[UpcomingReminder]
    past_due: list[UpcomingReminder]
    net_amount: float


class CashFlowProjection(SimplifiModel):
    from_date: str
    to_date: str
    starting_balance: float
    ending_balance: float
    points: list[dict[str, Any]]


class DashboardSnapshot(SimplifiModel):
    as_of: str
    account_count: int
    total_assets: float
    total_liabilities: float
    net_worth: float
    upcoming_bills_count: int
    upcoming_bills_total: float
    recent_transaction_count: int
