# Working with this client

Use the authenticated local API client first for Simplifi reads, previews and
authorized writes. Prefer an encrypted local transaction snapshot for analysis;
refresh selected originals before making changes. Use the existing scoped writers,
encrypted backups, concurrency checks and read-back verification.

Use browser integration only when the required operation is unsupported by the
client or cannot be completed reliably through the available API. A missing
client feature does not mean the underlying service has no API for it. Do not
silently weaken write guards or replay an uncertain write to avoid browser use.

When a browser fallback is necessary, explain the specific gap briefly and record
the observed behavior and implementation requirements in
[docs/api-gaps.md](docs/api-gaps.md). Check that document before rediscovering a
known limitation. Keep observations distinct from confirmed API contracts.

Keep account identities, household preferences, financial records, screenshots,
tokens, and real rule/transaction IDs in ignored encrypted local evidence. Source
documentation and tests must use portable paths and synthetic examples.
