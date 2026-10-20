# Account-aware analysis and incremental reviews

These commands extend an existing encrypted review. Reconciliation and analysis
operate offline; sync and rule boundary probes perform authenticated reads and
previews only. They do not change categories, rules, links or exclusions.
All identifiers below are synthetic. Store private inputs and outputs under
ignored `.local/`. CLI output contains readable financial information; do not
redirect it into tracked files.

## Follow cached account trails

```powershell
simplifi-review reconcile --review .local/review --transaction-id synthetic-credit --days 7 --max-hops 4
```

The helper returns exact decimal amount candidates, account details, ownership,
dates, investment activity, reciprocal link evidence and possible cash trails.
Known differing account currencies do not match; missing currency remains
unverified. Same-account positive-to-negative cash flows can connect investment
sale proceeds to outgoing journals. Bank-funded wallet payments require an
explicit source/account convention. A coincident amount alone never proves a match.

All ambiguous alternatives remain visible. Trails traverse the evidence graph in
either direction and are not necessarily chronological. Traversal is bounded by
the requested hop count and a path cap; `depth_limited` and `trails_truncated`
report these limits. `ambiguous` includes branches beyond the selected transaction.
Review the directed edges and missing-counterpart warnings before recording a
decision. Zero-dollar journals cannot establish a cash transfer. No helper
automatically records an offset or creates a transfer link.

## Record local analysis preferences

`preferences-set --stdin` validates and encrypts structured context. For example,
pipe this JSON from a private local input or interactive shell:

```json
{
  "version": 1,
  "account_owners": {"checking-one": "owner-one", "wallet-one": "owner-one"},
  "payee_aliases": {"Synthetic Cafe Variant": "Synthetic Cafe"},
  "statement_aliases": {"Synthetic Cafe": ["SYNTHETIC BILLING"]},
  "conventions": [{
    "id": "intentional-mix",
    "account_ids": ["checking-one"],
    "payee_contains": "Synthetic Cafe",
    "category_ids": ["dining-category", "coffee-category"],
    "note": "Different purchase types are intentional"
  }],
  "payment_channels": [{
    "bank_account_ids": ["checking-one"],
    "payment_account_id": "wallet-one",
    "statement_contains": "WALLET PAYMENT"
  }]
}
```

```powershell
simplifi-review preferences-set --review .local/review --stdin
simplifi-review preferences --review .local/review
simplifi-review analyze --review .local/review
```

Use existing account and category IDs from the review. Ownership partitions
same-payee analysis without guessing household membership. Explicit payee aliases
join known variants; statement aliases suppress known descriptor discrepancies.
Conventions may also specify boolean `reports_excluded` and `spending_excluded`.
Assignments outside a convention become candidates, including uniform but
unexpected assignments. Overlapping conventions must all agree before suppressing
a candidate. Mixed categories are candidates, not errors. Investment
actions are excluded from ordinary merchant-category analysis.

Preferences stay in the encrypted review and its history. Existing freeform notes
are not interpreted automatically. Preferences never create live rules. Reanalysis
preserves prior findings, numbers, rationale and decisions rather than silently
dismissing them when context changes.

## Refresh a review without replacing its history

```powershell
simplifi-review sync --review .local/review
simplifi-review sync --review .local/review --modified-after 2027-01-01T00:00:00Z
simplifi-review unresolved --review .local/review
simplifi-review render --review .local/review --output .local/review/report.html
```

Full sync reads the review date window and looks up previously cached records
missing from the list. A delta reads modifications without a date filter so it
can observe moved records and deletion tombstones. Delta absence is not deletion;
periodically use full sync. A missing exact lookup becomes a blocked evidence gap,
with its last-known data retained. Sync never guesses replacement IDs.

The immutable original snapshot remains intact. New records retain separate
first-seen evidence; history stores changed records rather than repeating the whole
snapshot on every sync. Known tombstones need no repeated lookup. Current state is
saved atomically. Existing finding IDs and
display numbers remain stable, and newly eligible members can join a finding.
Changed or missing evidence invalidates dependent decisions, execution outcomes
and reimbursement offsets so they must be reviewed again. History is retained.
Resolve uncertain execution journals before syncing: refreshed decisions can
invalidate previously prepared plans. Review and execution locks prevent competing
local writers; they do not lock remote account activity.

HTML is regenerated only by explicit `render`. It shows original and current
values, including first-seen records, and contains readable financial information.

## Investigate rule amount boundaries

```powershell
simplifi-rules amount-probe --rule-id synthetic-rule --transaction-id synthetic-penny --transaction-id synthetic-larger-credit --threshold 0 --threshold 0.01 --encrypted-output .local/amount-probe.dpapi
simplifi-rules amount-report --report .local/amount-probe.dpapi
```

The read-only probe preserves every other rule condition, sets historical
application false, saves actual preview requests/results encrypted, and rechecks
the rule and selected examples before saving. It compares exact signed decimal
amounts locally. Unsupported non-amount predicates remain unknown.

Inspection warns about the observed `GREATER_THAN 0` preview anomaly and strict
boundaries without rewriting thresholds or changing existing plan digests.
`GREATER_THAN 0.01` excludes exactly one cent in the local model and is not a
substitute for zero. Preview samples are not known to be exhaustive: absence
raises a suspected discrepancy, not proof of nonmatching. The backend cause
remains unconfirmed. No threshold workaround or live rule edit is applied.
