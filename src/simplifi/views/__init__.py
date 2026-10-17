"""Tab-shaped analytics views composed from raw resources."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from simplifi.models import (
    BillsIncomeUpcoming,
    CashFlowProjection,
    CategoryTotal,
    DashboardSnapshot,
    ExclusionLine,
    IncomeExpenseReport,
    MonthlyBurnReport,
    MonthlyBurnRow,
    MonthlySummary,
    SpendingByCategoryReport,
    SpendingPlanSummary,
    UpcomingReminder,
)
from simplifi.views.classify import (
    EXCLUSION_REASONS,
    category_id,
    classify_for_spending,
    transaction_amount,
    transaction_date,
    transaction_payee,
)


def _month_bounds(month: str) -> tuple[str, str]:
    """month is YYYY-MM."""
    start = date.fromisoformat(f"{month}-01")
    if start.month == 12:
        end = date(start.year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(start.year, start.month + 1, 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _today() -> str:
    return date.today().isoformat()


def _months_between(from_date: str, to_date: str) -> list[str]:
    start = date.fromisoformat(from_date[:7] + "-01")
    end = date.fromisoformat(to_date[:7] + "-01")
    months: list[str] = []
    cur = start
    while cur <= end:
        months.append(cur.strftime("%Y-%m"))
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return months


def _partition(
    transactions: list[dict[str, Any]],
    *,
    from_date: str,
    to_date: str,
    as_of: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[ExclusionLine]]:
    known_ids = {str(t.get("id")) for t in transactions if t.get("id")}
    spending: list[dict[str, Any]] = []
    income: list[dict[str, Any]] = []
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for txn in transactions:
        d = transaction_date(txn)
        if d is not None and (d < from_date or d > to_date):
            continue
        kind = classify_for_spending(txn, as_of=as_of, known_ids=known_ids)
        if kind == "spending":
            spending.append(txn)
        elif kind == "income":
            income.append(txn)
        else:
            buckets[kind].append(txn)

    exclusions: list[ExclusionLine] = []
    for kind, rows in buckets.items():
        exclusions.append(
            ExclusionLine(
                kind=kind,
                reason=EXCLUSION_REASONS.get(kind, kind),
                count=len(rows),
                net=sum(transaction_amount(t) for t in rows),
                transaction_ids=[str(t.get("id")) for t in rows if t.get("id")],
            )
        )
    return spending, income, exclusions


def spending_by_category(
    transactions: list[dict[str, Any]],
    *,
    from_date: str,
    to_date: str,
    as_of: str | None = None,
    category_names: dict[str, str] | None = None,
) -> SpendingByCategoryReport:
    as_of = as_of or _today()
    names = category_names or {}
    spending, _, exclusions = _partition(
        transactions, from_date=from_date, to_date=to_date, as_of=as_of
    )
    by_cat: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    for txn in spending:
        by_cat[category_id(txn)].append(txn)

    categories: list[CategoryTotal] = []
    for cid, rows in by_cat.items():
        total = sum(transaction_amount(t) for t in rows)
        categories.append(
            CategoryTotal(
                category_id=cid,
                category_name=names.get(cid or "", "(uncategorized)") if cid else "(uncategorized)",
                total=total,
                transaction_count=len(rows),
                transaction_ids=[str(t.get("id")) for t in rows if t.get("id")],
            )
        )
    categories.sort(key=lambda c: c.total)
    return SpendingByCategoryReport(
        from_date=from_date,
        to_date=to_date,
        as_of=as_of,
        categories=categories,
        exclusions=exclusions,
        spending_total=sum(c.total for c in categories),
    )


def monthly_burn(
    transactions: list[dict[str, Any]],
    *,
    from_date: str,
    to_date: str,
    as_of: str | None = None,
) -> MonthlyBurnReport:
    as_of = as_of or _today()
    spending, income, exclusions = _partition(
        transactions, from_date=from_date, to_date=to_date, as_of=as_of
    )
    months = _months_between(from_date, to_date)
    current_month = as_of[:7]
    rows: list[MonthlyBurnRow] = []
    complete_nets: list[float] = []

    for month in months:
        m_start, m_end = _month_bounds(month)
        out_txns = [t for t in spending if (d := transaction_date(t)) and m_start <= d <= m_end]
        in_txns = [t for t in income if (d := transaction_date(t)) and m_start <= d <= m_end]
        money_out = sum(transaction_amount(t) for t in out_txns)
        money_in = sum(transaction_amount(t) for t in in_txns)
        net = money_in + money_out
        complete = month < current_month
        if complete:
            complete_nets.append(net)
        rows.append(
            MonthlyBurnRow(
                month=month,
                money_out=money_out,
                money_in=money_in,
                net=net,
                complete=complete,
                outflow_transaction_ids=[str(t.get("id")) for t in out_txns if t.get("id")],
                inflow_transaction_ids=[str(t.get("id")) for t in in_txns if t.get("id")],
            )
        )

    avg = sum(complete_nets) / len(complete_nets) if complete_nets else None
    return MonthlyBurnReport(
        from_date=from_date,
        to_date=to_date,
        as_of=as_of,
        months=rows,
        average_net_complete_months=avg,
        exclusions=exclusions,
    )


def income_vs_expense(
    transactions: list[dict[str, Any]],
    *,
    from_date: str,
    to_date: str,
    as_of: str | None = None,
) -> IncomeExpenseReport:
    as_of = as_of or _today()
    spending, income, exclusions = _partition(
        transactions, from_date=from_date, to_date=to_date, as_of=as_of
    )
    income_total = sum(transaction_amount(t) for t in income)
    expense_total = sum(transaction_amount(t) for t in spending)
    return IncomeExpenseReport(
        from_date=from_date,
        to_date=to_date,
        as_of=as_of,
        income=income_total,
        expense=expense_total,
        net=income_total + expense_total,
        income_transaction_ids=[str(t.get("id")) for t in income if t.get("id")],
        expense_transaction_ids=[str(t.get("id")) for t in spending if t.get("id")],
        exclusions=exclusions,
    )


def monthly_summary(
    transactions: list[dict[str, Any]],
    *,
    month: str,
    as_of: str | None = None,
    category_names: dict[str, str] | None = None,
    top_n: int = 5,
) -> MonthlySummary:
    from_date, to_date = _month_bounds(month)
    as_of = as_of or _today()
    report = income_vs_expense(transactions, from_date=from_date, to_date=to_date, as_of=as_of)
    by_cat = spending_by_category(
        transactions,
        from_date=from_date,
        to_date=to_date,
        as_of=as_of,
        category_names=category_names,
    )
    spending, _, _ = _partition(transactions, from_date=from_date, to_date=to_date, as_of=as_of)
    payee_totals: dict[str, float] = defaultdict(float)
    payee_counts: dict[str, int] = defaultdict(int)
    for txn in spending:
        payee = transaction_payee(txn)
        payee_totals[payee] += transaction_amount(txn)
        payee_counts[payee] += 1
    top_payees = sorted(
        (
            {"payee": p, "total": total, "count": payee_counts[p]}
            for p, total in payee_totals.items()
        ),
        key=lambda row: row["total"],
    )[:top_n]

    return MonthlySummary(
        month=month,
        income=report.income,
        expense=report.expense,
        net=report.net,
        top_categories=by_cat.categories[:top_n],
        top_payees=top_payees,
    )


def _scheduled_amount(item: dict[str, Any]) -> float:
    for key in ("amount", "dueAmount", "averageAmount"):
        if item.get(key) is not None:
            return float(item[key])
    return 0.0


def _scheduled_due(item: dict[str, Any]) -> str | None:
    for key in ("dueOn", "nextDueOn", "date", "scheduledOn"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value[:10]
    return None


def _scheduled_type(item: dict[str, Any]) -> str:
    return str(item.get("type") or item.get("recurrenceType") or item.get("kind") or "").upper()


def spending_plan_summary(
    *,
    month: str,
    transactions: list[dict[str, Any]],
    scheduled: list[dict[str, Any]],
    goals: list[dict[str, Any]] | None = None,
    as_of: str | None = None,
) -> SpendingPlanSummary:
    from_date, to_date = _month_bounds(month)
    as_of = as_of or _today()
    spending, income, _ = _partition(
        transactions, from_date=from_date, to_date=to_date, as_of=as_of
    )

    month_sched = []
    for item in scheduled:
        due = _scheduled_due(item)
        if due is None or from_date <= due <= to_date:
            month_sched.append(item)

    bills = 0.0
    subscriptions = 0.0
    sched_income = 0.0
    recurring_by_id = {}
    for item in month_sched:
        amount = _scheduled_amount(item)
        stype = _scheduled_type(item)
        if amount > 0 and ("INCOME" in stype or ("BILL" not in stype and "SUB" not in stype)):
            sched_income += amount
            continue
        if "SUB" in stype:
            subscriptions += abs(amount)
        else:
            bills += abs(amount)
        if item.get("id"):
            key = str(item["id"])
            bucket = "subscription" if "SUB" in stype else "bill"
            previous = recurring_by_id.get(key, (bucket, 0.0))[1]
            recurring_by_id[key] = (bucket, previous + abs(amount))

    goals_total = 0.0
    for goal in goals or []:
        for key in ("monthlyContribution", "contributionAmount", "targetMonthly"):
            if goal.get(key) is not None:
                goals_total += abs(float(goal[key]))
                break

    income_total = sum(transaction_amount(t) for t in income) or sched_income
    # Scheduled buckets cover bills/subscriptions. Actual linked payments belong
    # in those buckets too, rather than being subtracted again as other spending.
    actual_bills = actual_subscriptions = 0.0
    linked_actuals = defaultdict(float)
    other_transactions = []
    for txn in spending:
        linked = txn.get("stModelId")
        if linked is not None and str(linked) in recurring_by_id:
            linked_actuals[str(linked)] += abs(transaction_amount(txn))
        elif txn.get("isSubscription"):
            actual_subscriptions += abs(transaction_amount(txn))
        elif txn.get("isBill"):
            actual_bills += abs(transaction_amount(txn))
        else:
            other_transactions.append(txn)
    linked_bills = linked_subscriptions = 0.0
    for key, actual in linked_actuals.items():
        bucket, planned = recurring_by_id[key]
        if bucket == "subscription":
            subscriptions -= planned
            linked_subscriptions += max(planned, actual)
        else:
            bills -= planned
            linked_bills += max(planned, actual)
    # Without linkage, the category's planned/actual totals provide an estimate;
    # do not guess payment identity from merchant names or equal amounts.
    bills = linked_bills + max(bills, actual_bills)
    subscriptions = linked_subscriptions + max(subscriptions, actual_subscriptions)
    other_spend = abs(sum(transaction_amount(t) for t in other_transactions))
    left = income_total - bills - subscriptions - goals_total - other_spend

    return SpendingPlanSummary(
        month=month,
        income=income_total,
        bills=bills,
        subscriptions=subscriptions,
        goals=goals_total,
        other_spend=other_spend,
        left_this_month=left,
        scheduled_count=len(month_sched),
        transaction_count=len(spending) + len(income),
    )


def bills_income_upcoming(
    scheduled: list[dict[str, Any]],
    *,
    from_date: str,
    to_date: str,
    as_of: str | None = None,
) -> BillsIncomeUpcoming:
    as_of = as_of or _today()
    reminders: list[UpcomingReminder] = []
    past_due: list[UpcomingReminder] = []
    net = 0.0
    for item in scheduled:
        due = _scheduled_due(item)
        if due is None:
            continue
        if due < from_date or due > to_date:
            continue
        amount = _scheduled_amount(item)
        net += amount
        reminder = UpcomingReminder(
            id=str(item["id"]) if item.get("id") else None,
            payee=transaction_payee(item)
            if item.get("payee") or item.get("payeeName")
            else (
                str(item.get("name") or item.get("payee") or item.get("payeeName") or "") or None
            ),
            amount=amount,
            due_on=due,
            type=_scheduled_type(item) or None,
            raw=item,
        )
        if due < as_of:
            past_due.append(reminder)
        else:
            reminders.append(reminder)
    reminders.sort(key=lambda r: r.due_on or "")
    past_due.sort(key=lambda r: r.due_on or "")
    return BillsIncomeUpcoming(
        from_date=from_date,
        to_date=to_date,
        reminders=reminders,
        past_due=past_due,
        net_amount=net,
    )


def cash_flow_projection(
    scheduled: list[dict[str, Any]],
    *,
    starting_balance: float,
    from_date: str,
    to_date: str,
) -> CashFlowProjection:
    events: list[tuple[str, float, dict[str, Any]]] = []
    for item in scheduled:
        due = _scheduled_due(item)
        if due is None or due < from_date or due > to_date:
            continue
        amount = _scheduled_amount(item)
        # Bills usually stored positive in UI but reduce cash; normalize by type.
        stype = _scheduled_type(item)
        if "INCOME" in stype:
            signed = abs(amount)
        elif amount > 0 and ("BILL" in stype or "SUB" in stype or "EXPENSE" in stype):
            signed = -abs(amount)
        else:
            signed = amount
        events.append((due, signed, item))
    events.sort(key=lambda e: e[0])

    balance = starting_balance
    points: list[dict[str, Any]] = [{"date": from_date, "balance": balance, "event": None}]
    for due, signed, item in events:
        balance += signed
        points.append(
            {
                "date": due,
                "balance": balance,
                "amount": signed,
                "payee": item.get("payee") or item.get("name"),
                "id": item.get("id"),
            }
        )
    return CashFlowProjection(
        from_date=from_date,
        to_date=to_date,
        starting_balance=starting_balance,
        ending_balance=balance,
        points=points,
    )


def dashboard_snapshot(
    *,
    accounts: list[dict[str, Any]],
    scheduled: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    as_of: str | None = None,
    upcoming_days: int = 14,
) -> DashboardSnapshot:
    as_of = as_of or _today()
    end = (date.fromisoformat(as_of) + timedelta(days=upcoming_days)).isoformat()

    asset_types = {"INVESTMENT", "BANK", "REAL_ESTATE", "VEHICLE", "CASH", "OTHER_ASSET"}
    liability_types = {"LOAN", "CREDIT", "OTHER_LIABILITY"}
    assets = 0.0
    liabilities = 0.0
    for acct in accounts:
        if acct.get("isClosed") or acct.get("closed"):
            continue
        balance = acct.get("onlineBalance")
        if balance is None:
            balance = acct.get("currentBalance") or acct.get("balance") or 0
        balance_f = float(balance or 0)
        atype = str(acct.get("type") or "").upper()
        if atype in liability_types or balance_f < 0 and atype not in asset_types:
            liabilities += abs(balance_f)
        else:
            assets += balance_f

    upcoming = bills_income_upcoming(scheduled, from_date=as_of, to_date=end, as_of=as_of)
    recent_from = (date.fromisoformat(as_of) - timedelta(days=7)).isoformat()
    recent = [t for t in transactions if (d := transaction_date(t)) and recent_from <= d <= as_of]

    return DashboardSnapshot(
        as_of=as_of,
        account_count=len([a for a in accounts if not (a.get("isClosed") or a.get("closed"))]),
        total_assets=assets,
        total_liabilities=liabilities,
        net_worth=assets - liabilities,
        upcoming_bills_count=len(upcoming.reminders) + len(upcoming.past_due),
        upcoming_bills_total=sum(
            abs(r.amount or 0) for r in upcoming.reminders + upcoming.past_due
        ),
        recent_transaction_count=len(recent),
    )


class Views:
    """Bound views that pull from a Client-like resource provider when needed."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def _provided_or_fetch(self, kwargs, name, fetch):
        value = kwargs.pop(name, None)
        return fetch() if value is None else value

    def spending_by_category(self, **kwargs: Any) -> SpendingByCategoryReport:
        txns = self._provided_or_fetch(kwargs, "transactions", self._client.transactions)
        cats = kwargs.pop("categories", None)
        if "category_names" not in kwargs:
            cats = cats if cats is not None else self._client.categories()
            kwargs["category_names"] = {
                str(c["id"]): str(c.get("name") or c.get("title") or c["id"])
                for c in cats
                if c.get("id")
            }
        return spending_by_category(txns, **kwargs)

    def monthly_burn(self, **kwargs: Any) -> MonthlyBurnReport:
        txns = self._provided_or_fetch(kwargs, "transactions", self._client.transactions)
        return monthly_burn(txns, **kwargs)

    def income_vs_expense(self, **kwargs: Any) -> IncomeExpenseReport:
        txns = self._provided_or_fetch(kwargs, "transactions", self._client.transactions)
        return income_vs_expense(txns, **kwargs)

    def monthly_summary(self, **kwargs: Any) -> MonthlySummary:
        txns = self._provided_or_fetch(kwargs, "transactions", self._client.transactions)
        if "category_names" not in kwargs:
            cats = self._client.categories()
            kwargs["category_names"] = {
                str(c["id"]): str(c.get("name") or c.get("title") or c["id"])
                for c in cats
                if c.get("id")
            }
        return monthly_summary(txns, **kwargs)

    def spending_plan_summary(self, month: str, **kwargs: Any) -> SpendingPlanSummary:
        txns = self._provided_or_fetch(kwargs, "transactions", self._client.transactions)
        scheduled = self._provided_or_fetch(
            kwargs, "scheduled", self._client.scheduled_transactions
        )
        goals = kwargs.pop("goals", None)
        if goals is None:
            goals = self._client.goals()
        return spending_plan_summary(
            month=month, transactions=txns, scheduled=scheduled, goals=goals, **kwargs
        )

    def bills_income_upcoming(self, **kwargs: Any) -> BillsIncomeUpcoming:
        scheduled = self._provided_or_fetch(
            kwargs, "scheduled", self._client.scheduled_transactions
        )
        return bills_income_upcoming(scheduled, **kwargs)

    def cash_flow_projection(self, **kwargs: Any) -> CashFlowProjection:
        scheduled = self._provided_or_fetch(
            kwargs, "scheduled", self._client.scheduled_transactions
        )
        if "starting_balance" not in kwargs:
            accounts = self._client.accounts()
            kwargs["starting_balance"] = sum(
                float(a.get("onlineBalance") or a.get("balance") or 0)
                for a in accounts
                if str(a.get("type") or "").upper() in {"BANK", "CASH"}
            )
        return cash_flow_projection(scheduled, **kwargs)

    def dashboard_snapshot(self, **kwargs: Any) -> DashboardSnapshot:
        accounts = self._provided_or_fetch(kwargs, "accounts", self._client.accounts)
        scheduled = self._provided_or_fetch(
            kwargs, "scheduled", self._client.scheduled_transactions
        )
        transactions = self._provided_or_fetch(kwargs, "transactions", self._client.transactions)
        return dashboard_snapshot(
            accounts=accounts,
            scheduled=scheduled,
            transactions=transactions,
            **kwargs,
        )
