# Local authentication

Preferred: `browser-login.cmd` or `run.ps1 browser-login`. Complete sign-in and
MFA yourself on Quicken's website in the dedicated browser window. The helper
captures only the successful token endpoint response from that isolated context,
verifies direct API reads, then saves the tokens using Windows DPAPI. It preserves
existing tokens if verification fails. It does not inspect form fields, request
bodies, your existing browser profile, or exported storage state.

Use `login.cmd` or `run.ps1 login` and enter credentials in the local terminal.
See [the README](../README.md#authentication-and-data-handling) for storage, scope,
and limitations. This fork uses Windows DPAPI and does not support plaintext
token-file import. No live authentication has been verified until a local check
succeeds. Never share your password, MFA, HAR files, or tokens in chat.

Terminal sign-in revision `session-state-v2` includes the session-state headers
and MFA request-ID handoff observed in the production website on 2026-10-17.
The browser flow lets Quicken manage those details directly. Diagnostic errors
contain HTTP status and validated QCS codes only; response details are suppressed.
