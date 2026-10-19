# Local client workflow

Locate the checkout containing `pyproject.toml`, `src/simplifi/review_cli.py`, and `docs/transaction-reviews.md`. Read that documentation and `docs/api-gaps.md` for current capabilities. Use the configured Python environment; `python -m simplifi.review_cli` is equivalent to `simplifi-review`.

## Start or resume

For an existing review, inspect `summary` and `inventory`, then use `simplifi.reviews.load_review` for transaction evidence and context. Inventory lists findings, categories, and decisions, not complete transactions. Use the canonical directory rather than a legacy import source. Offline legacy import requires a preview digest and separate destination; it performs no live writes.

For a new review, substitute the requested dates and private directory:

```text
simplifi-review download --review .local/category-review --start 2027-01-01 --end 2027-12-31
simplifi-review summary --review .local/category-review
simplifi-review inventory --review .local/category-review
```

Supply `--dataset-id` before the command if needed. Downloads require authentication; local inspection, decisions, and rendering do not. Use the login helper when required; do not solicit credentials or tokens in chat.

## Record and execute decisions

Use current category IDs; disambiguate duplicate names. Pipe decision lists through `decide --review <directory> --stdin` or use encrypted input with `--decisions`. Never save live decisions in plaintext source. Synthetic example:

```json
[
  {
    "id": "meals-decision",
    "finding_id": "finding-example",
    "transaction_ids": ["transaction-a", "transaction-b"],
    "action": "category",
    "category_id": "restaurants-category",
    "exceptions": {"transaction-b": "camping-category"},
    "note": "One payment reimbursed camping rather than dinner"
  }
]
```

Actions `confirm`, `dismiss`, and `unresolved` accept notes but no target category. Reuse a decision ID to revise it. A category decision can record a user-approved pairing with `offsets: {"credit-id": "expense-id"}`. Find candidates with:

```text
simplifi-review candidates --review .local/category-review --credit-id example-credit --days-before 45 --days-after 3
```

Choose an appropriate window. Candidate matching does not decide a pairing or create transfer links.

Select explicit decision IDs to avoid executing other pending decisions:

```text
simplifi-review plan --review .local/category-review --decision-id meals-decision --plan .local/meals-plan.dpapi
simplifi-review inspect --plan .local/meals-plan.dpapi
simplifi-review execute --review .local/category-review --plan .local/meals-plan.dpapi --confirm <inspection-digest> --journal .local/meals-journal
simplifi-review status --journal .local/meals-journal
simplifi-review render --review .local/category-review --output .local/category-review/report.html
```

The digest binds exact operations and dataset; it does not grant authorization. Resume the same plan/journal after interruption so reads reconcile dispatch state. Do not replace a plan to bypass uncertainty. Avoid concurrent website edits: local locks cannot remove the server-side GET-to-PUT race.

## Capability boundaries and persistence

The category plan executor currently supports unsplit, unlinked CASH_FLOW category edits and confirmations. It blocks investments and linked transfers deliberately. Generic transfer classification has a separate scoped `simplifi.category_update.update_transfer_classification` writer. Inspect its current contract; use only for authorized existing cash-flow records with encrypted backup and read-back. It does not create reciprocal links. Do not force transfer classification into a category plan.

Rules, exclusion flags, transfer links, recurring links, and payee/memo edits require their own supported operations. Check implementation before assuming availability. After UI edits, false flags may be omitted and null memos may become empty strings; verify effective flags and content without relaxing general validation.

Use `record_decisions` for local dispositions. When projecting a verified separate scoped operation into a review, inspect current persistence/history formats, retain immutable originals, lock both review and execution stores, validate the typed model, and atomically update current state with an encrypted receipt referenced in history. A confirmation alone does not prove a live correction. Keep unresolved import evidence flagged.

Prefer library functions over one-off mutation scripts. Store operation evidence under ignored `.local/` with `save_encrypted`; never overwrite a backup to retry. Use `replace_encrypted` only for mutable projections under the appropriate lock. Runtime validation needs explicit exceptions rather than assertions.
