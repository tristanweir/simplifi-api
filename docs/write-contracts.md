# Scoped write contracts

These contracts were traced offline from saved public Simplifi application bundles
on 2026-10-19. Development and regression tests made no live account requests.
The request shapes are supported by frontend code, but the new operations have not
been exercised against a live backend by this implementation. Unexpected results
stop execution and require inspection; a successful HTTP status is insufficient.

## Evidence provenance

Public assets from `https://simplifi.quicken.com/`:

- `main.7ec2b778e6504377fc10.js`, SHA-256
  `4643c59ebadd19546e65345920a9045109df396427f1b68822cb15ff78d36e86`.
- `pnpm.app-components.85bd1c062c8750b253d5.js`, SHA-256
  `ebad97c17cbf5bb484a7a49885c32ddb45257369179744bcbb445766e4d3f98f`.

The bundles are not vendored. No authenticated response bodies, account identities,
tokens, or browser captures are required in source. Search terms below identify
the relevant frontend functions without reproducing the bundles.

| Operation | Frontend request | Evidence and implemented boundary |
|---|---|---|
| Payee/memo | `PUT /transactions/{id}` with CASH_FLOW transform | `transformTransactionToRequestData` and its common field transform include `payee` and `memo`. Explicit text fields are composed with supported category/flag edits. |
| Transfer link/unlink | `PUT /transactions` with `{"resources": [...]}` | `transformTransactionsToRequestData`, `qcsSyncUpdateResources`, `createRequiredTransferTxns`, and transfer preprocessing show the batch envelope, existing counterpart references, and uncategorizing/removing links. Only two existing unsplit cash-flow records may be sent. Both original dates, signs, notes and exclusions are preserved; no create/delete records are included. |
| Rule creation | `POST /transaction-rules` with rule transform | The rule API's `create` method uses the same transform as previews/updates and expects an ID/resource response. Creation plans use a stable `clientId`, omit server IDs and ordering metadata, and force `applyToExistingTransactions: false`. |
| Recurring unlink | `PUT /transactions/{id}/unlink-to-scheduled-txn` with `{"id": "...", "action": "unlink-to-scheduled-txn"}` | The main bundle dispatches the action with a transaction ID. The transaction action saga builds the action path, copies the action payload, and uses `qcsSyncUpdateResource`, which sends PUT. Only a selected bank cash-flow record with a known series and bank identity is eligible. |

Transfer pairs use explicit reciprocal `transfer.id` and ACCOUNT references rather
than date/amount auto-matching. The frontend can otherwise create a counterpart,
change the counterpart date, or delete a manually entered counterpart. This client
never requests those behaviors. The actual preservation of both sides must be
established by reads; batch atomicity is not assumed.

Recurring unlink reconciliation handles either an unchanged ID or exactly one new
record with the original bank identity and preserved financial fields. An old-ID
lookup failure alone is not used as evidence. Other transactions and every series
must remain unchanged. Missing, duplicate, or changed replacement evidence blocks
completion and never causes automatic replay.

Rule creation requires exactly one added rule matching the stable client ID and
all selected conditions/actions. If the service drops the client ID, recovery is
blocked rather than guessing from a same-looking rule. Existing rule changes are
rejected except for the reciprocal ordering pointers of a validated insertion.
Transaction snapshots must remain unchanged before and after future-only creation.

These additional snapshot comparisons are intentionally conservative. Concurrent
sync or website edits can block reconciliation even when the requested operation
succeeded. Retain the encrypted plan, backups, and journal, then inspect read-only
state. Local locks do not supply server conditional writes or cross-process token
cache coordination. No uncertain write is automatically retried or compensated.
