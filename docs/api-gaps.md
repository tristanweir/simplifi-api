# API gaps and browser fallbacks

Use this list to prioritize client improvements. These are observed limitations
of this client or workflows, not claims that Quicken lacks an underlying endpoint.
No private account evidence belongs in this document.

## Supported API operations

- Paginated transaction/category/account/rule reads and encrypted local analysis.
- Scoped category changes on ordinary, unsplit `CASH_FLOW` records that already
  have a `CATEGORY` assignment or the observed `UNCATEGORIZED/0` classification,
  and no transfer link: `update_category`.
- Typed transaction review decisions, encrypted category plans and journals,
  offline legacy import, reimbursement candidates, and regenerated HTML reports:
  `simplifi-review`. See [transaction reviews](transaction-reviews.md).
- Generic Transfer classification on eligible existing cash-flow records, with
  explicit incoming/outgoing direction: `update_transfer_classification`. This
  accepts categories, generic Transfer, and the observed `UNCATEGORIZED/0` state;
  it preserves exclusion flags and does not create a linked counterpart or move funds.
- Rule previews, updates and deletions, including encrypted resumable change plans.
- Generic Transfer direction and transaction Reports/Spending Plan exclusions in
  typed resumable transaction plans; flags can be combined with category or
  Transfer classification. Only eligible unlinked cash-flow records are supported.
- Finding lookup by number, unresolved lists, local confirmations/evidence notes,
  selected-record refresh, and verified typed receipt import through `simplifi-review`.
- Explicit payee/memo edits in existing transaction plans, preserving unselected notes.
- Dedicated two-record transfer link/unlink plans and single-transaction recurring
  unlink plans, including replacement-ID reconciliation and review projection.
- Future-only rule creation plans with previews, stable client identity, encrypted
  backups/journals, and verification that existing transactions remain unchanged.

## Operation boundaries and retained observations

### Create transaction rules

Implemented in `simplifi.rule_creation` and the rules CLI's `create-plan`,
`create-review`, and `create-execute` commands. Existing update/delete plan formats
are preserved. Creation forces future-only application, rejects duplicate conditions,
and reconciles the stable client identity before considering completion. A dropped
client ID or ambiguous result blocks recovery; it never triggers another POST.

### Link existing transfer counterparts

Implemented in `simplifi.relationship_plans` using an exact batch for two existing
records. Both must be unsplit cash-flow transactions in distinct accounts with
known equal currencies, opposite amounts and no recurring association. Automatic
matching and counterpart creation are not used. Dates, signs, notes and exclusions
must survive read-back, along with reciprocal account/transaction references.
Investment, split and multi-currency transfers remain unsupported.

### Remove incorrect account-transfer links

Implemented as an explicit reciprocal two-record relationship plan. Both resulting
classifications must be provided. The counterpart is preserved even when manual;
no deletion or implicit reclassification is requested. Both links and financial
details are checked, with existing pending-record normalization kept narrow.

Observed UI expense reclassification cleared both exclusion flags on the outgoing
record and its unlinked deposit counterpart. An excluded destination account did
not preserve the deposit's transaction flags. Restore explicitly requested deposit
exclusions and verify both sides; preserve amounts, dates and note contents.
The UI also normalized absent notes from null to an empty string.

### Extend exclusion edits to additional record types

Implemented for eligible unlinked, unsplit cash-flow records through typed plans
and `update_transaction_fields`. Ordinary category edits still preserve flags unless
changes are explicitly requested. Account defaults are never edited. Split,
investment and linked-transfer flag edits remain unsupported. Tests use synthetic
responses; the new plan integration has not been exercised against live data.
The UI may clear a disabled flag by omitting it from the API response rather than
returning literal false. Verify its effective boolean state and the UI checkbox;
an unrelated account default must not be changed to edit a single transaction.

### Remove an incorrect recurring-series association

Implemented as the separate `unlink_recurring` relationship action. It binds the
selected association and bank identity and verifies that all series and unrelated
records are unchanged. This remains separate from payee/category authorization.

An observed unlink replaced the transaction ID while retaining bank description,
account, date, amount, payee and category. A GET of the old ID then returned HTTP
400. A fresh paginated read found one replacement record without `stModelId` or
`isBill`. Other matching metadata was still present; `matchedTxn` alone does not
prove a recurring association remains.

The executor supports a retained ID or one verified replacement, updating the
review replacement map atomically. Missing or ambiguous replacement evidence blocks
completion without replay. An old-ID lookup failure is not proof the write failed.

### Edit payee or memo alongside category

Implemented with explicit `text_changes` in typed transaction plans and
`update_transaction_fields`. `update_category` still changes only a category.
Unselected notes are preserved; an empty memo explicitly clears them. Arbitrary
whole-record edits, linked/split/investment text edits, and implicit rule/series
changes remain unsupported.

## Observed pitfalls to retain in tests and investigation

- **Investment category review:** standard spending categories in raw investment
  records can be misleading. Inspect transaction type, investment action, and
  exclusion flags. Do not rewrite trades just because a raw category resembles a
  merchant-spending category. The investment UI displays investment actions.
- **UI amount sign change:** selecting generic Transfer for an incoming
  Payment/Deposit record in an investment account flipped its amount negative.
  Read-back caught this; the scoped API classification writer restored the exact
  positive deposit amount. Verify direction as well as magnitude for every edit.
- **Zero amount threshold in rule previews:** observed `GREATER_THAN` with
  `amount1: 0` returned no matches, while `amount1: 0.01` matched known positive
  payouts. Treat the cause as unconfirmed. The working rule excludes exactly
  one-cent amounts; disclose that limitation rather than claiming it matches every
  incoming amount. Preview without mutating live rules before changing thresholds.
- **Browser timeouts after a successful save:** read the exact record and relevant
  counterpart before considering another write. A timeout is not proof of failure.
- **Category-write boundaries:** unrecognized uncategorized classifications, split records,
  investments, linked transfers, and payee/memo edits are outside the ordinary
  category writer's contract. Implement and test a separate scoped capability when
  needed; do not remove the guards as a workaround.

The new request shapes were traced from saved public frontend code and tested with
synthetic responses. No live account data was changed during implementation. See
[write contracts](write-contracts.md) for provenance, verification boundaries and
remaining server concurrency limitations.

Private request/response evidence, exact account mappings and transaction identifiers
belong under ignored local storage. Commit only generalized findings and synthetic
regression tests.
