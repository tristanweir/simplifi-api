# Transaction rules

`Client.transaction_rules()` lists raw API-format rules. `Client.transaction_rule(id)`
finds one active rule through that listing. These are distinct from alert rules.

The ordinary HTTP client stays read-only. The opt-in rules module permits only one
exact preview request or one exact approved rule update per request session.
Creating, deleting, reordering, and bulk-changing rules are not exposed.

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
still race between a final read and the PUT. Live rule reads and an unchanged-rule
preview have been verified; actual rule writes have only synthetic mocked tests.
No real rule or transaction was changed while implementing this feature.
