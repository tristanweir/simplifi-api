# Local authentication

**Platform requirement:** Persistent sign-in and refresh across process restarts
require Windows DPAPI. Authenticate separately on each Windows machine under the
Windows user who will run the client. macOS and Linux currently have no supported
token-storage backend.

Preferred: `browser-login.cmd` or `run.ps1 browser-login`. Complete sign-in and
MFA yourself on Quicken's website in the dedicated browser window. The helper
captures the successful token response from that isolated context and extracts
public client metadata from the matching OAuth exchange request. It discards all
other request fields, including the authorization code. After direct API reads
succeed, it saves the tokens and available metadata together using Windows DPAPI.
Failed verification preserves the existing cache. The capture is scoped to the
token exchange and needs no access to your existing browser profile or storage.

The client uses the cached metadata for refresh after a restart. If the cache
predates this change and public metadata discovery fails, run browser sign-in once
to seed the cache. If Quicken explicitly rejects the cached client, the client
tries discovery and retries once only with changed metadata. A rejected refresh
token requires a new sign-in. A generic HTTP failure leaves the cache intact.

Use `login.cmd` or `run.ps1 login` and enter credentials in the local terminal.
See [the README](../README.md#authentication-and-data-handling) for storage, scope,
and limitations. This fork uses Windows DPAPI and does not support plaintext
token-file import. No live authentication has been verified until a local check
succeeds. Never share your password, MFA, HAR files, or tokens in chat.

Terminal sign-in revision `session-state-v2` includes the session-state headers
and MFA request-ID handoff observed in the production website on 2026-10-17.
The browser flow lets Quicken manage those details directly. Diagnostic errors
contain HTTP status and validated QCS codes only; response details are suppressed.
