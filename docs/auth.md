# Authentication

## Interactive login (recommended)

```bash
pip install -e .
simplifi login
```

You will be prompted for email/password. If Quicken issues MFA, enter the code when
asked. Access and refresh tokens are stored at `~/.simplifi/tokens.json` (mode 0600)
or `SIMPLIFI_TOKEN_PATH`.

Later commands reuse the refresh token when possible — no MFA on every run.

## Environment variables

See `.env.example`:

| Variable | Purpose |
|----------|---------|
| `SIMPLIFI_EMAIL` / `SIMPLIFI_PASSWORD` | Login credentials |
| `SIMPLIFI_DATASET_ID` | Optional override of `qcs-dataset-id` |
| `SIMPLIFI_THREAT_METRIX_SESSION_ID` | Optional browser session id if login fails |
| `SIMPLIFI_TOKEN_PATH` | Custom token file location |

## Finding your dataset id

Simplifi identifies the household with the `qcs-dataset-id` header. This package
normally resolves it via `GET /datasets`. To capture it from the browser:

1. Sign in at https://simplifi.quicken.com
2. DevTools → Network → any `services.quicken.com` request
3. Read Request Headers → `qcs-dataset-id`

## Manual access token

```python
from simplifi import Client

client = Client()
client.use_token("...", refresh_token="...")  # refresh optional but recommended
```

## Security notes

- Never commit tokens, HARs, or transaction exports.
- This client is **read-only** by construction (mutating HTTP methods raise).
- Comply with Quicken Simplifi Terms of Service; use only against accounts you own.
