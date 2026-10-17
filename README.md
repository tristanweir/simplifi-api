# Simplifi API client

Unofficial Python client for Quicken Simplifi Personal, forked from
[soluwalana/simplifi-api](https://github.com/soluwalana/simplifi-api). The original
MIT license and Git history are preserved.

This fork adds browser-assisted authentication, Windows-encrypted token storage,
safer HTTP handling, and an explicitly scoped category-update API. The ordinary
`Client` and CLI remain read-only. It is not affiliated with Quicken; private
endpoints can change without notice.

## Set up on another computer

Authentication storage currently requires **Windows**, Python 3.10 or later, and
installed Chrome or Edge. Sign in separately on every computer. Do not copy token
caches, browser profiles, transaction exports, or backups between machines.

```powershell
git clone https://github.com/tristanweir/simplifi-api.git
cd simplifi-api
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e ".[browser,dev]"
.\.venv\Scripts\python.exe -m simplifi.local_cli browser-login
```

Alternatively run `setup.ps1 -Python <python-executable>`, followed by
`browser-login.cmd`. The lock file pins the tested runtime and development packages.
The package's optional dependency groups also support ordinary unpinned installation:
`python -m pip install -e ".[browser,dev]"`.

The launcher uses the repository's `.venv`; `SIMPLIFI_PYTHON` can override its
interpreter. It does not depend on a Codex installation or a machine-specific path.
Only Windows token storage is implemented; other systems fail rather than save
plaintext credentials.

## Browser login and reads

```powershell
& .\run.ps1 browser-login
# For Edge:
& .\run.ps1 browser-login -Browser msedge
& .\run.ps1 check
& .\run.ps1 export -Resource transactions -Since 2026-10-01 -OutputFile .\exports\transactions.json
& .\run.ps1 export -Resource categories -OutputFile .\exports\categories.json
& .\run.ps1 logout
```

Complete sign-in and MFA yourself on Quicken's website in the separate window.
The helper observes only the successful token endpoint response from that isolated
browser context. It does not read login form fields, request bodies, your normal
browser profile, or unrelated response bodies. It saves no HAR, screenshots,
traces, or browser-storage exports. Close the dedicated window to cancel; sign-in
times out after ten minutes. Do not open unrelated sites in that window.

After capture, the browser closes. Candidate tokens stay in memory until direct
profile, dataset, account, and transaction reads succeed. Only then are the tokens
saved. A failed verification preserves the existing cache. `check` prints counts
and creates a timestamped receipt, without printing financial records. Exports
are plaintext and explicitly requested; existing output files are not overwritten.

`login.cmd` retains the older terminal password/MFA flow, but browser login is
preferred. Secrets are never accepted as CLI arguments. The Python library retains
upstream resource and view helpers; the smaller CLI exposes accounts, transactions,
categories, and tags. See [endpoint map](docs/endpoint-map.md).

## Authentication and data handling

- Access/refresh tokens are encrypted with current-user Windows DPAPI in
  `.local/tokens.dpapi`, with atomic replacement and no plaintext fallback.
- Expired tokens and rejected reads can refresh once; rotated tokens are saved.
  Run one client process at a time against a shared cache.
- The public web app's OAuth client metadata is resolved from Quicken's public
  application bundle when needed; no credential-shaped app constant is embedded.
- Authenticated traffic stays on HTTPS `services.quicken.com`. Redirects are
  blocked, certificate verification stays enabled, and environment proxy settings
  and `.netrc` are not inherited. Public metadata lookup uses a separate, unauthenticated
  session restricted to `https://simplifi.quicken.com`.
- API error bodies are suppressed; auth errors expose only validated QCS codes.
  Token object representations omit token values.
- Disable `DEBUG`, `PWDEBUG`, and `SSLKEYLOGFILE` before browser login. The helper
  refuses those diagnostic settings to avoid accidental secret logging.
- `.local/`, `.env*`, token caches, backups, HAR files, and `exports/` are ignored
  by Git. Use the ignored export/backup locations for financial data.
- `logout` deletes the local cache; it does not revoke the server session. DPAPI
  does not protect against other programs already running as the same Windows user.
  Python cannot guarantee erasure of secrets from process memory.

## Explicit category updates

`simplifi.category_update.update_category` is a separate opt-in API for one selected
ordinary, unsplit CASH_FLOW transaction. It requires a freshly read original,
an assignable target category, and a new encrypted backup path. It rejects
transfer/split records and concurrent changes detected before the write.

The writer permits exactly one PUT to the selected transaction with the approved
category payload. It never retries a write automatically. It reads the transaction
back and checks the category and other fields. It recognizes the server's observed
normalization of bank-pending source and modification metadata.

```python
from pathlib import Path
from simplifi import Client
from simplifi.category_update import update_category

client = Client()
client.resolve_dataset()  # Supply dataset_id explicitly if multiple datasets exist.
original = client.transaction("selected-transaction-id")
category = next(c for c in client.categories() if c["id"] == "selected-category-id")
verified = update_category(
    client, original, category,
    backup_path=Path(".local/original-category-change.dpapi"),
)
```

The write shape follows the web application's CASH_FLOW transform. That transform
does not submit `dbVersion`, so a concurrent edit between the last read and PUT
cannot be excluded. A timeout means the outcome is uncertain: read the transaction
before considering a retry. Restoring a category requires a fresh read and the
original category; never replay a stale whole-record backup.

## Transaction rules

`Client.transaction_rules()` and `Client.transaction_rule(id)` read transaction
rules. The opt-in `simplifi.transaction_rules` module adds dry-run previews,
encrypted plans/backups, explicit confirmation, and scoped rule updates/deletions.
`simplifi.rule_plans` adds reusable future-only batches, encrypted execution journals,
read-back verification, and safe resume without replaying uncertain writes. CLI
commands include `inspect`, `plan`, `review`, `execute`, `delete`, and `status`.
The older single-update workflow retains separately authorized historical application.
See [transaction-rule workflow](docs/transaction-rules.md) for API
field names, CLI commands, preview coverage, and backfill limitations.

## Check reconciliation skill

The repository includes
[schwab-check-reconciliation](.agents/skills/schwab-check-reconciliation/SKILL.md)
for matching Simplifi checks to Schwab records and reading payees from check images.
It favors local cached transaction reads, verifies account/number/amount matches,
and reports uncertainty before any separately authorized edits. The skill contains
no account data; private evidence stays in ignored local storage.

## Tests and contributing upstream

Bound analytics views fetch inputs only when omitted or `None`; an explicit empty
list means an empty dataset. Goal read failures propagate rather than silently
reporting zero contributions. Spending-plan summaries keep recurring payments out
of other spending and reconcile known `stModelId` links per scheduled item. Where
link metadata is absent, flagged bills/subscriptions use aggregate planned-versus-
actual budget estimates; no merchant-name or equal-amount matching is inferred.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
git remote add upstream https://github.com/soluwalana/simplifi-api.git
git fetch upstream
```

Tests use synthetic fixtures and mocked API calls, plus real Windows DPAPI checks.
They do not authenticate to a live account or require a browser download. GitHub
Actions runs the suite on Windows/Python 3.10 and 3.12.

For a focused upstream PR, branch from `upstream/main` and cherry-pick the relevant
commits from this fork. Keep browser/authentication changes separate from the opt-in
category writer: upstream's existing contribution policy is read-only. See
[contributing](docs/CONTRIBUTING.md) for the workflow. No PR is created automatically.

## License

MIT. See [LICENSE](LICENSE).
