# Contributing

This fork preserves [soluwalana/simplifi-api](https://github.com/soluwalana/simplifi-api)
as upstream. Keep changes small enough to review and contribute independently.

## Principles

1. The normal Client and CLI stay read-only. The category writer is explicit and scoped.
2. Personal accounts only; no Business & Personal write flows.
3. All committed fixtures must be synthetic. Never commit authentication state, HARs,
   transaction exports, household details, local absolute paths, or encrypted backups.
4. Document endpoint changes in `docs/endpoint-map.md`.
5. Authentication errors must not contain response bodies, token values, or passwords.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[browser,dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

Windows is required for the DPAPI-backed store. Tests never require a live account.
Use a fresh temporary directory if specifying pytest `--basetemp`.

## Upstream PR workflow

```powershell
git remote -v
# If upstream is absent:
git remote add upstream https://github.com/soluwalana/simplifi-api.git
git fetch upstream
git switch -c feature/focused-auth-fix upstream/main
git cherry-pick <relevant-commit-sha>
git push -u origin feature/focused-auth-fix
```

Open a PR with base `soluwalana/simplifi-api:main` and compare
`tristanweir/simplifi-api:feature/focused-auth-fix`. Describe the user-visible behavior,
tests, and compatibility limits. Upstream's original policy is read-only, so do not
include the opt-in category-write commit in an authentication PR. Coordinate any
proposed write support with its maintainer separately.

Before pushing, inspect `git diff --cached`, the staged file list, and newly added
history for credentials, household details, or machine-specific paths. Keep token
caches and backups out of Git even though they are encrypted.
