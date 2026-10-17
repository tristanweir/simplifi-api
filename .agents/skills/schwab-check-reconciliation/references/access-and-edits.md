# Access and editing notes

## Simplifi local client

Resolve paths relative to the current repository, not a previous machine. Use the
configured local environment. In this repository:

- `simplifi.client.Client` offers `resolve_dataset()`, `accounts()`,
  `transactions(date_on_after=...)`, `transaction(id)`, and `categories()`.
  Select the dataset explicitly when multiple datasets exist.
- Transaction reads follow pagination. Apply the requested upper date boundary
  locally; the high-level client accepts a lower date bound, not an upper bound.
  Use `simplifi.views.classify.transaction_date()` and inspect `cpData` for imported
  descriptions. Check numbers may appear only in the description; do not assume
  every transaction has a structured check field.
- For existing DPAPI snapshots, inspect their schema and timestamp before use;
  snapshot filenames and directories are not universal. The repository's
  `simplifi.transaction_rules.load_encrypted` / `save_encrypted` helpers also
  support local JSON evidence with `.dpapi` filenames. They use Windows DPAPI;
  saving creates a new file exclusively. Keep it under ignored `.local/` storage.
  DPAPI evidence and token caches are tied to the Windows user/environment. On
  another computer, authenticate separately and reacquire needed evidence.
- The ordinary HTTP client is read-only. Do not change its method allowlist or
  reuse a rule-update writer for transaction edits.

For comparison and totals, parse amounts as `Decimal` from their decimal string,
or use integer cents. Preserve the raw original. Reject unrecognized formats
instead of rounding them into a match.

## Browser fallback and Schwab images

Reuse the intended signed-in browser tabs. Reconfirm the selected account and
date filter before searching. Do not infer account identity from an amount alone.

The observed Schwab history workflow used a paid-check row's **More** button to
open **More Details**. The image sometimes appeared below the initially visible
part of the dialog and required scrolling. These are discovery hints, not fixed
selectors: re-inspect the current page. Close the prior dialog before selecting
another row and confirm the new check number and amount before reading its image.
If an image cannot be read through the supported browser surface, report it as
unavailable; do not extract browser credentials or invent a private bank endpoint.

When Simplifi API access is unavailable, use its browser transaction search. The
observed UI supported queries such as `check is:uncategorized date:START..END` and
`amount=VALUE`. These narrow candidates; verify the individual row's account,
number, date, amount, and category. Search semantics and controls may change.

## Authorized changes

Inspect the installed client version before choosing a writer. In this repository,
`simplifi.category_update.update_category` supports one ordinary, unsplit
`CASH_FLOW` transaction that already has a `CATEGORY` assignment and no transfer.
It rejects truly `UNCATEGORIZED` entries and does not expose payee or memo edits.
It checks the original against a fresh read, saves an encrypted backup, performs
one scoped category write, and verifies the result. Do not bypass these guards to
handle an unsupported check.

For an authorized edit outside that capability, use the supported Simplifi UI or
an explicitly implemented and tested scoped writer. In the UI, identify the field
by its visible label rather than a remembered textbox position. Appending a payee
means retaining the existing check description and check number; adding a note
means preserving existing memo content unless replacement was requested. Do not
accept an option to create a rule or change other matching transactions without
authorization for that scope.

Before applying, record the selected originals and requested changes in encrypted
local evidence. After saving, reopen/read the exact transaction and verify both
the requested values and preservation of unrelated fields. A tool timeout or a
disappearing row in an uncategorized search does not prove success: search by
stable identity and read back before deciding whether to retry. Resume from this
verified inventory rather than rerunning every edit.
