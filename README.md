# quicken-simplifi-api

Unofficial **read-only** Python client for **Quicken Simplifi (Personal)**.

This package talks to the same `services.quicken.com` backend used by
[simplifi.quicken.com](https://simplifi.quicken.com/), covering Personal sidebar
tabs (Dashboard, Spending Plan, Bills & Income, Goals, Investments, Watchlist,
Reports, Transactions, Settings reads) via API calls plus local aggregations
where the web UI computes views client-side.

> **Not affiliated with Quicken.** There is no official public Simplifi developer
> API. Use against accounts you own, at your own risk, and in accordance with
> Quicken Simplifi’s Terms of Service. Quicken may change or revoke endpoints
> without notice.

## Install

```bash
pip install git+https://github.com/soluwalana/simplifi-api.git
# or from a checkout:
pip install -e ".[dev]"
```

## Quick start

```bash
simplifi login
simplifi export transactions -o exports/transactions.json
simplifi export spending-plan --month 2026-08
simplifi export dashboard
```

```python
from simplifi import Client

client = Client()
client.login()  # uses SIMPLIFI_EMAIL / SIMPLIFI_PASSWORD, prompts for MFA

accounts = client.accounts()
txns = client.transactions(date_on_after="2025-01-01")
plan = client.views.spending_plan_summary(month="2026-08")
report = client.views.spending_by_category(from_date="2026-01-01", to_date="2026-08-29")
```

Tokens (access + refresh) are stored under `~/.simplifi/tokens.json` so cron jobs
can refresh without MFA when Quicken allows it.

## What is included

| Area | Support |
|------|---------|
| Auth + refresh tokens | Yes |
| Accounts, transactions, categories, tags, datasets | Yes |
| Scheduled transactions (Bills & Income) | Yes |
| Goals, watchlists, securities/holdings | Best-effort paths |
| Spending Plan / Reports / Dashboard views | Local aggregations |
| Business & Personal tabs | **No** |
| Writes (edit/categorize/link FI) | **No** |

See [docs/endpoint-map.md](docs/endpoint-map.md) and [docs/auth.md](docs/auth.md).

## Discovering new endpoints

When Quicken ships SPA changes:

```bash
# After exporting a HAR from DevTools while clicking Personal tabs:
python scripts/parse_har.py path/to/session.har
python scripts/probe_endpoints.py   # requires prior `simplifi login`
```

Do **not** commit HAR files or real transaction exports.

## Relationship to other projects

Inspired by [rijn/simplifiapi](https://github.com/rijn/simplifiapi/) and the
read-only patterns in community MCP servers. This package focuses on broader
Personal tab coverage, refresh-token persistence, and tab-shaped views in a
publishable Python library named `quicken-simplifi-api` (import: `simplifi`).

## License

MIT — see [LICENSE](LICENSE).
