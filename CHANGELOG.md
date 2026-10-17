# Changelog

## Unreleased

- Add reusable future-only transaction-rule update/delete change plans and CLI review.
- Add encrypted original backups and append-only execution checkpoints with OS locking.
- Reconcile interrupted writes through server reads; never automatically replay an
  uncertain mutation. Check stale rules/previews and verify completed operations.
- Keep legacy single-update commands compatible, including explicit historical application.

## 0.1.0

- Initial read-only Personal Simplifi client (`quicken-simplifi-api`).
- Auth with MFA + refresh-token persistence.
- Core resources: datasets, accounts, transactions (incremental filters), categories, tags.
- Personal resources: scheduled transactions, goals, watchlists, securities/holdings,
  free-to-spend/planned-spending candidates, preferences, alerts, institution logins,
  report configurations.
- Tab-shaped views: spending plan summary, spending by category, monthly burn,
  income vs expense, monthly summary, bills/income upcoming, cash flow projection,
  dashboard snapshot.
- CLI: `simplifi login`, `simplifi export …`
- HAR parser and endpoint probe scripts.
