# Transaction reviews

Use `simplifi-review` (or `python -m simplifi.review_cli`) after browser login.
Only `execute` changes live transactions. Rules, transfer links and recurring
associations require separate operations.

## Start a review

```powershell
simplifi-review download --review .local/review --start 2026-01-01 --end 2026-12-31
simplifi-review summary --review .local/review
simplifi-review inventory --review .local/review
simplifi-review render --review .local/review --output .local/review/report.html
```

Pass `--dataset-id` before the command when multiple datasets exist. Findings flag
same-payee category differences and bank-description discrepancies for review;
neither necessarily indicates an error. Merchant analysis excludes investments
and linked transfers.

Snapshots, decisions and history use Windows-user DPAPI encryption. `inventory`,
`inspect` and `candidates` display financial details. `render` creates readable
HTML without scripts or external resources; keep it under ignored `.local/`.

## Record decisions

Use finding and category IDs from `inventory`. The following synthetic example
sets a group to Restaurants, with a transaction-specific exception:

```powershell
@'
[
  {
    "id": "decision-meals",
    "finding_id": "example-finding-id",
    "transaction_ids": ["example-1", "example-2"],
    "action": "category",
    "category_id": "restaurant-category-id",
    "exceptions": {"example-2": "camping-category-id"},
    "note": "Second payment reimbursed camping"
  }
]
'@ | simplifi-review decide --review .local/review --stdin
```

Alternatively, pass `--decisions .local/decisions.dpapi` for a list saved with
`save_encrypted`. Reuse a decision ID to revise it. Selections and exceptions must
use transaction IDs within the finding; conflicting decisions block preparation.
The library's `resolve_category` accepts category names when unambiguous.

Use `confirm` to verify a correct record without a PUT, or `dismiss` / `unresolved`
for local-only dispositions. These actions accept notes but no category targets.

## Prepare, inspect and execute

```powershell
simplifi-review plan --review .local/review --decision-id decision-meals --plan .local/meal-plan.dpapi
simplifi-review inspect --plan .local/meal-plan.dpapi
simplifi-review execute --review .local/review --plan .local/meal-plan.dpapi --confirm <digest-from-inspect> --journal .local/meal-journal
simplifi-review status --journal .local/meal-journal
```

Omitting `--decision-id` selects all category and confirmation decisions. Plans
bind the dataset, refreshed originals, exact payloads and decisions to the digest.
Preparation and execution reject changed data; a confirmation also requires the
live record to match the review snapshot.

Category edits support unsplit CASH_FLOW records with a category or the observed
`UNCATEGORIZED/0` classification. Linked transfers and investments are blocked.
Amounts and signs are preserved, with the same pending-record normalization checks
as the single-category writer.

The executor checks the whole batch, saves originals and dispatch intent, and
rechecks each record before its one-use write. Writes never retry automatically.
Resume the same command after interruption: reads reconcile a dispatched result.
If success cannot be proved, execution stops—even if a crash occurred before the
request was sent. Retain the journal and resolve that outcome before replanning.

Verified results update the encrypted review atomically. A failed local update can
resume without replaying writes. Rerun `render` to refresh the report.

Locks prevent competing executions and decision changes within one review.
There is no confirmed server-side conditional-write contract, so outside edits
between GET and PUT remain possible. Run one process per token cache and avoid
simultaneous website edits to selected records.

## Reimbursement evidence

```powershell
simplifi-review candidates --review .local/review --credit-id example-credit --days-before 45 --days-after 3
```

Candidates use exact decimal amounts within the inclusive window and never select
a match automatically.
Add `"offsets": {"example-credit": "example-expense"}` to a category decision to
record an accepted pairing. Searches exclude accepted expenses; one expense cannot
offset two credits. User-confirmed pairings may have a nonzero amount difference.
Older or unrecorded offsets still require investigation. No transfer links are created.

## Import an existing review

```powershell
simplifi-review import-preview --source .local/legacy-review --start 2026-01-01 --end 2026-12-31
simplifi-review import-legacy --source .local/legacy-review --destination .local/imported-review --start 2026-01-01 --end 2026-12-31 --confirm <import-digest>
simplifi-review render --review .local/imported-review --output .local/imported-review/report.html
```

Import is offline and requires the preview digest and a separate destination.
It retains finding numbers, replacement IDs and original files, copying encrypted
evidence with a hash manifest under `legacy-evidence/`.

Matching saved receipts become historical confirmations, not new write requests.
Missing or conflicting evidence remains flagged. HTML and notes provide context
but cannot establish write success; import does not verify live account state.

Library entry points are `prepare_transaction_plan`, `inspect_transaction_plan`,
`execute_transaction_plan`, and `transaction_plan_status` in
`simplifi.transaction_plans`. Review storage, analysis and rendering live in
`simplifi.reviews`; versioned models are in `simplifi.review_models`.

See [API gaps](api-gaps.md) for unsupported operations.
