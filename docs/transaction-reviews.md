# Transaction reviews

Use `simplifi-review` (or `python -m simplifi.review_cli`) after browser login.
Only `execute` changes live transactions. Transfer classification and transaction
exclusions are supported; rules, transfer links and recurring
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

## Transfer direction and exclusions

Use `action: "transfer"` with explicit `incoming: true` or `false` to classify an
existing eligible cash-flow record as generic Transfer. This preserves magnitude
and sets the selected sign. It does not move money or link/create a counterpart.
Add `exclusions` to a category or Transfer decision to change flags alongside its
classification, or use `action: "exclusions"` for flags alone:

```json
[
  {
    "id": "journal-transfer",
    "finding_id": "example-finding-id",
    "transaction_ids": ["example-journal"],
    "action": "transfer",
    "incoming": true,
    "exclusions": {"isExcludedFromReports": true, "isExcludedFromF2S": true},
    "note": "Existing sale proceeds journaled between accounts"
  }
]
```

`isExcludedFromF2S` controls Spending Plan exclusion. Only explicit booleans are
accepted. Unselected flags and account defaults remain unchanged. A disabled flag
may be omitted in the response; verification accepts missing as false only for
explicitly edited flags, and rejects numeric/string lookalikes. Inspection shows
before/after amounts, classification and both flags. Overlapping decisions must
agree; combine classification and flags into one decision for the same record.

Transfer and flag-only edits additionally accept existing generic Transfer
(`BALANCE_ADJUSTMENT/2`). Flag-only edits allow zero amounts; Transfer direction
requires a finite nonzero amount. All edits reject split, investment, linked-transfer
and deleted records. Use the same plan/inspect/execute/status commands for these
operations. Old category plan wire formats and confirmation digests remain valid.

## Manage a review

```powershell
simplifi-review show --review .local/review --number 7
simplifi-review unresolved --review .local/review
'Confirmed merchant purpose' | simplifi-review confirm --review .local/review --number 7 --transaction-id example-1 --note-stdin --decision-id confirmed-example
'Matching journal evidence' | simplifi-review evidence --review .local/review --number 7 --transaction-id example-1 --note-stdin
simplifi-review refresh --review .local/review --transaction-id example-1
```

`show` includes original/current values, decisions, import issues and attached
evidence. `confirm` records a local disposition without authentication or changing
the live reviewed flag. Use a confirmation plan when live verification is required.
Evidence notes do not prove a write succeeded or resolve missing receipts.
Explanations are read from stdin and saved inside the encrypted review; use
synthetic examples in shell history rather than private notes.

`refresh` performs selected API reads, never writes, preserving the original
snapshot and finding numbers. Changed records return to unresolved, invalidate
selected prepared decisions and current execution outcomes, and retain historical
receipts. Reimbursement decisions depending on changed expenses are invalidated too.
Resolve uncertain execution through its journal before refreshing or revising its
decisions. Refresh does not infer replacement IDs from similar records. Rendering
remains a separate explicit command.

### Import a separate verified operation

Use `receipt-inspect --receipt .local/receipt.dpapi`, then
`receipt-import --review .local/review --receipt .local/receipt.dpapi --number 7 --confirm <receipt-digest>`.
Import performs API reads only. It requires exact dataset/review identity, original
evidence matching the cached record, a valid scoped payload, a verified result,
and a matching live read. Reimporting an identical receipt is idempotent. A stale
or conflicting receipt is rejected without updating the review.

The encrypted input uses `simplifi.review_models.TransactionReceipt`: version 1,
kind `transaction-receipt`, `dataset_id`, `review_id`, `verified_at`, a typed
`TransactionOperation` in `operation`, and the complete read-back record in `result`.
Use `save_encrypted` and `.to_wire()` to save it. Arbitrary legacy response shapes
are not treated as verified receipts. Import records history, updates current state,
confirms the selected finding entry, and resolves matching import issues for that
entry. It never executes the operation contained in the receipt.
