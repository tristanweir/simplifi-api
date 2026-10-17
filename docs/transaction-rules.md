# Transaction rules

`Client.transaction_rules()` lists raw API-format rules. `Client.transaction_rule(id)`
finds one active rule through that listing. These are distinct from alert rules.

The ordinary HTTP client stays read-only. The opt-in rules module permits only one
exact preview, approved rule update, or approved rule deletion per request session.
The reusable `simplifi.rule_plans` workflow coordinates updates/deletions with
encrypted plans and a durable execution journal. Creating and reordering rules
are not exposed. Ordinary reads never enable mutations.

## Reusable change plans (recommended)

Use stable API rule IDs, not row numbers from a report. Inspect a selection first:

```powershell
.\.venv\Scripts\python.exe -m simplifi.rules_cli inspect --rule-id selected-rule-id
```

Supply a JSON array of operations, or an encrypted `.dpapi` array saved with
`transaction_rules.save_encrypted`. Example input, saved under ignored `.local/`:

```json
[
  {"action": "update", "rule_id": "selected-rule-id", "changes": {
    "coaTargetCondition": {"type": "CATEGORY", "id": "selected-category-id"},
    "amountSourceCondition": null
  }},
  {"action": "delete", "rule_id": "obsolete-rule-id"}
]
```

`null` removes an optional field. Unmentioned fields are preserved. Each rule may
appear only once. Batch plans are **future-only** and reject historical application.
Preparing a plan snapshots all rules, validates the entire operation list, obtains
read-only server previews for updates, and detects rule changes during planning.
Deletion review shows the complete original rule; deletion has no transaction preview.

```powershell
.\.venv\Scripts\python.exe -m simplifi.rules_cli plan --operations .local/changes.json --plan .local/batch.dpapi
.\.venv\Scripts\python.exe -m simplifi.rules_cli review --plan .local/batch.dpapi
.\.venv\Scripts\python.exe -m simplifi.rules_cli execute --plan .local/batch.dpapi --confirm DIGEST_FROM_REVIEW --journal .local/batch-run
.\.venv\Scripts\python.exe -m simplifi.rules_cli status --journal .local/batch-run
```

`review` and `status` are local-only and do not instantiate an authenticated client.
Review output contains private rule details: avoid redirecting it into tracked files.
Plaintext operation inputs are optional; encrypted inputs avoid storing those details
in plaintext. Plan, backup, and checkpoint contents always use current-user Windows
DPAPI with no plaintext fallback. Keep inputs and journals under ignored `.local/`.

Single deletions use the same implementation:

```powershell
.\.venv\Scripts\python.exe -m simplifi.rules_cli plan --delete-rule-id obsolete-rule-id --plan .local/delete.dpapi
.\.venv\Scripts\python.exe -m simplifi.rules_cli delete --plan .local/delete.dpapi --confirm DIGEST_FROM_REVIEW --journal .local/delete-run
```

Python equivalents are `prepare_change_plan`, `inspect_change_plan`,
`execute_change_plan`, `change_plan_status`, `prepare_rule_deletion`, and
`apply_rule_deletion` in `simplifi.rule_plans`. The CLI `execute` command handles
both updates and deletions; `delete` accepts only a single-deletion plan.

## Safe resume and verification

Internally, strict Pydantic `ChangePlan`, update/delete operation models and typed
journal states validate the workflow. The `JournalStore` owns locking, encrypted
backups and durable checkpoints; rule reconciliation is a pure state transition.
The version-1 JSON shape is preserved, including unknown server fields in raw rule
records, so existing encrypted batch plans retain their confirmation digests and
existing journals can resume. Invalid states, skipped transitions and malformed
commands fail before dispatch. Historical single-update plans remain a separate API.

Tests may inject a `JournalStore` with a `JsonStorage` implementation through
`execute_change_plan(..., journal_store=...)`; production defaults continue to use
DPAPI, exclusive writes and the selected journal's OS lock. Custom storage is a
trusted library extension and must preserve its durability/confidentiality contract.

Run the **same execute command, plan, confirmation, and journal directory** to resume.
The executor holds a local OS file lock and appends encrypted checkpoints with
exclusive creation and flushed writes. Each operation has these states:

- `pending`: not dispatched; original rule and preview must still match.
- `dispatched`: encrypted original backup and checkpoint saved before the request.
- `verified`: server reads confirmed the requested fields or the deleted rule's absence.

On resume, verified operations are checked and skipped. A dispatched operation is
read first: if its result matches, it becomes verified without sending another write.
If the original remains or the result conflicts, execution **stops without retrying**.
A delayed request may still be in flight. Inspect and resolve the server outcome;
do not bypass this by deleting the journal or immediately starting another execution.

Changed previews, changed unrelated rules, concurrent edits to completed rules,
different plans/datasets, corrupt checkpoints, and checkpoint gaps stop execution.
A crash before dispatch can resume using the existing matching encrypted backup.
A torn checkpoint fails closed and requires manual recovery; it is not discarded.
For a legitimately stale plan, resolve any dispatched outcome first, then prepare
a fresh plan for the remaining operations with a new journal.

This is a sequential, partially resumable workflow, **not an atomic transaction**:
earlier verified changes remain if a later step stops. There is no automatic rollback.
Read checks are not server locks, so concurrent edits can still race with a write.
Matching read-back after an interrupted request establishes the desired current
state, not which writer produced it. Verification checks rule state; it does not
snapshot all historical transactions or claim historical reconciliation.

DPAPI state is tied to the current Windows identity/machine. On another computer,
sign in there and prepare fresh plans; copying encrypted tokens/journals is not a
portable migration. Keep original-machine backups for audit and manual recovery.

## List and preview

```powershell
.\.venv\Scripts\python.exe -m simplifi.rules_cli list
# Keep the full listing encrypted instead of printing it:
.\.venv\Scripts\python.exe -m simplifi.rules_cli list --encrypted-output .local/rules.dpapi
```

Supply a changes JSON file using remote API field names, for example:

```json
{"coaTargetCondition": {"type": "CATEGORY", "id": "selected-category-id"}}
```

Use category IDs returned by `Client.categories()`. An optional field set to JSON
`null` is removed from the outgoing rule payload. Matching/action fields that are
not mentioned are preserved. IDs, deletion, ordering, and arbitrary fields cannot
be changed through this interface.

```powershell
.\.venv\Scripts\python.exe -m simplifi.rules_cli preview --rule-id selected-rule-id --changes .local/changes.json --plan .local/preview-plan.dpapi
```

Preview POSTs to `/categories-aggregation/preview/txn-rule`, the same endpoint used
on the web application's review screen. It does not save the proposed rule. The
result includes a local before/after diff, matching counts, sample count, the
server's preview boundary, and a confirmation digest. The original rule, payload,
and full server preview are saved together with Windows DPAPI encryption.
Plan and backup files are created exclusively; existing files are not overwritten.

Preview transactions may be a sample. The server's `srcTxnsCount`, `resultTxnsCount`,
and `previewTxnsEndDate` are retained as returned; preview coverage must not be
interpreted as a guarantee covering all historical transactions.

## Existing-transaction effects

[Simplifi's rule documentation](https://support.simplifi.quicken.com/en/articles/4792733-how-to-use-transaction-rules-in-the-web-app)
says an edited rule can optionally apply to existing transactions. The web app warns
that historical updates run in the background. Therefore a saved rule read-back
confirms the rule, not the completion of every historical transaction update.

Every new plan explicitly sets `applyToExistingTransactions` to **false** by default,
even when the original saved rule contains true. Existing rules' stored values
reflect their saved settings; they are not implicit authorization for a new backfill.
The API normally applies rules to matching incoming transactions; documented exclusions
include manual transactions, transfers, balance adjustments, and recurring transactions.

To deliberately plan a historical application, pass `--apply-existing` to preview.
To execute it, the same flag must also be supplied to update. The API equivalents
are `apply_to_existing=True` and a separate `allow_existing=True`. Restoring the rule
cannot automatically reverse historical transaction changes; any such restoration
requires separate transaction backups and an explicit reconciliation plan.

## Execute a reviewed plan

```powershell
.\.venv\Scripts\python.exe -m simplifi.rules_cli update --plan .local/preview-plan.dpapi --confirm DIGEST_FROM_PREVIEW --backup .local/before-update.dpapi
```

The update checks the plan digest and dataset, refetches the rule to detect concurrent
edits, and repeats the preview. A changed rule or preview requires a new plan. An
encrypted backup is saved before the single PUT to `/transaction-rules/{id}`.
There are no automatic write retries. After a timeout or ambiguous result, read the
rule before considering another write. A final read verifies intended fields.

These are client-side concurrency checks, not an atomic server lock. Updates can
still race between a final read and the PUT. The older single-update commands above
remain available for compatibility, but do not maintain a resumable journal. Prefer
the change-plan workflow for future-only changes and deletions. Refactor tests use
synthetic rules and mocked requests; they never authenticate to a live account.
