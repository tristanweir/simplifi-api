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

## Missing client operations

### Create transaction rules

The reusable rules executor supports updates/deletions, but not creation. Browser
rule creation was used for authorized future-only rules. The existing preview
request session can preview a proposed creation payload without a saved rule ID.

Add a typed creation plan and a one-use scoped create session after verifying the
request contract. Persist a checkpoint before dispatch; on an uncertain result,
reconcile the rule list before any retry to avoid duplicate rules. Verify conditions,
actions, historical-application settings, and preservation of existing rule actions.
Do not broaden the ordinary HTTP method allowlist.

### Link existing transfer counterparts

The category writer rejects existing transfers and only accepts category targets;
the generic Transfer writer deliberately does not link records. The UI can set
the outgoing record's category to the destination account and automatically match
an existing incoming record with a different posted date.

Add a dedicated plan binding the dataset, both transaction IDs, accounts, exact
opposite amounts and originals. Verify reciprocal `transfer.id` references and
`coa` account references on both existing records. Preserve amount direction,
account, posted date, payee and notes. Do not create a new counterpart merely
because automatic matching failed. Categorizing a ledger record is distinct from
initiating an actual movement of money.

### Remove incorrect account-transfer links

The current category guard rejects a record with transfer metadata. In the UI,
changing its account-transfer category to an expense category removes the link on
both sides. The unrelated counterpart remains present but may become uncategorized.

Implement this as an explicitly authorized two-record operation with encrypted
originals for both sides. Review the counterpart's resulting classification;
do not silently delete or reclassify it. Verify both links are removed and both
records' financial details preserved. Include tests for bank-imported and merged
pending/cleared records and automatic exclusion-flag changes.

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

There is no scoped client operation for this. The transaction detail UI offers
Linked to series → Unlink → Update. This changes recurring tracking separately
from a payee/category correction and needs authorization for that scope.

An observed unlink replaced the transaction ID while retaining bank description,
account, date, amount, payee and category. A GET of the old ID then returned HTTP
400. A fresh paginated read found one replacement record without `stModelId` or
`isBill`. Other matching metadata was still present; `matchedTxn` alone does not
prove a recurring association remains.

An implementation must bind the specific series association, capture the original
and resulting IDs, detect ambiguous replacements, and reconcile before retrying.
Verify that the series itself and other transactions are preserved. Do not treat
an old-ID lookup failure as proof the write failed.

### Edit payee or memo alongside category

`update_category` exposes only a category edit. Browser editing was used to restore
bank-supported payee names alongside categories. Add explicit typed editable fields
and exact payload binding rather than exposing arbitrary whole-record updates.
Preserve existing notes unless their replacement was authorized. Creating a rule
or unlinking a series is a separate operation.

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

Private request/response evidence, exact account mappings and transaction identifiers
belong under ignored local storage. Commit only generalized findings and synthetic
regression tests.
