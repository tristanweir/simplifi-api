# Endpoint map (Personal Simplifi)

Unofficial map of [simplifi.quicken.com](https://simplifi.quicken.com/) Personal tabs to
`services.quicken.com` calls and `simplifi.Client` methods.

Quicken does not publish a public developer API. Paths below come from the web SPA
(Redux action inventory), community clients, and HAR discovery. Some alternate paths
are tried automatically and may 404 on certain tenants — treat those as soft
availability.

Update this table when you run `scripts/parse_har.py` against a fresh HAR.

## Auth

| UI / flow | Method | Path | Package |
|-----------|--------|------|---------|
| Login | POST | `/oauth/authorize` | `Client.login` / `AuthSession.login` |
| Token | POST | `/oauth/token` | `AuthSession.exchange_code` / `refresh` |
| Profile | GET | `/userprofiles/me` | `Client.profile` |

Headers on data calls: `Authorization: Bearer …`, `Qcs-Dataset-Id`, `app-client-id`,
`app-release`, `app-build`.

## Core data

| UI tab | Method | Path | Package |
|--------|--------|------|---------|
| (household) | GET | `/datasets` | `Client.datasets` / `resolve_dataset` |
| Accounts / Dashboard | GET | `/accounts` | `Client.accounts` |
| Account detail | GET | `/accounts/{id}` | `Client.account` |
| Transactions | GET | `/transactions` | `Client.transactions` |
| Single transaction | GET | `/transactions/{id}` | `Client.transaction` |
| Earliest date | POST | `/transactions/earliest-date-on` | `Client.transactions_earliest_date_on` |
| Categories (Settings) | GET | `/categories` | `Client.categories` |
| Tags (Settings) | GET | `/tags` | `Client.tags` |

Query params commonly used: `limit`, `dateOnAfter`, `modifiedAfter`, pagination via
`metaData.nextLink`.

## Bills & Income

| UI tab | Method | Path | Package |
|--------|--------|------|---------|
| Bills & Income / Overview | GET | `/scheduled-transactions` | `Client.scheduled_transactions` |
| Cash Flow (view) | — | local aggregation | `Client.views.cash_flow_projection` |
| Upcoming (view) | — | local aggregation | `Client.views.bills_income_upcoming` |

## Spending Plan

| UI section | Method | Path | Package |
|------------|--------|------|---------|
| Free to spend / plan inputs | GET | `/free-to-spend` (candidates) | `Client.free_to_spend` |
| Planned spend items | GET | `/planned-spending` (candidates) | `Client.planned_spending` |
| Monthly summary (view) | — | local aggregation | `Client.views.spending_plan_summary` |

## Goals / Watchlist / Investments

| UI tab | Method | Path | Package |
|--------|--------|------|---------|
| Savings Goals | GET | `/goals` (candidates) | `Client.goals` |
| Goal activity | GET | `/goals/{id}/transactions` (candidates) | `Client.goal_transactions` |
| Watchlist | GET | `/spending-watchlists` (candidates) | `Client.spending_watchlists` |
| Investments | GET | `/v2/investments/securities` | `Client.securities` |
| Holdings | GET | `/investments/holdings` (candidates) | `Client.holdings` |

## Reports (mostly client-side)

| UI report | Method | Path | Package |
|-----------|--------|------|---------|
| Saved configs | GET | `/report-configurations` (candidates) | `Client.report_configurations` |
| Spending by category | — | local aggregation | `Client.views.spending_by_category` |
| Income & expense | — | local aggregation | `Client.views.income_vs_expense` |
| Monthly burn | — | local aggregation | `Client.views.monthly_burn` |
| Monthly summary | — | local aggregation | `Client.views.monthly_summary` |

## Settings / misc (read)

| UI | Method | Path | Package |
|----|--------|------|---------|
| Preferences | GET | `/preferences` | `Client.preferences` |
| Filters | GET | `/filters` | `Client.filters` |
| Alert rules | GET | `/alert-rules` | `Client.alert_rules` |
| Alerts | GET | `/alerts` | `Client.alerts` |
| Institution logins | GET | `/institution-logins` | `Client.institution_logins` |
| Institutions | GET | `/institutions` | `Client.institutions` |
| Feature flags | GET | `/feature-flags` | `Client.feature_flags` |
| Entitlements | GET | `/entitlements` | `Client.entitlements` |
| Transfer history | GET | `/transfers/balance` | `Client.transfers_balance` |
| Dashboard snapshot | — | local aggregation | `Client.views.dashboard_snapshot` |

## Out of scope (Business & Personal / writes)

Not implemented: Sales & Billing, invoices, mileage, tax planner business features,
FI linking (Plaid/Finicity), or any PUT/PATCH/DELETE of financial records.

## Discovery workflow

1. Capture a HAR while clicking Personal tabs.
2. `python scripts/parse_har.py export.har`
3. Diff against this document; open a PR with path updates.
4. Optionally `python scripts/probe_endpoints.py` with a stored login.
