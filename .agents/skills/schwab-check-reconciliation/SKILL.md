---
name: schwab-check-reconciliation
description: Reconcile Simplifi check transactions with Schwab paid-check records and check images to identify payees, verify amounts, and flag unresolved matches. Use for check-image lookup, unidentified checks, or checking payment records against cleared checks.
---

# Schwab Check Reconciliation

Identify who each check was made out to using the bank image, with a traceable match
back to the correct Simplifi transaction. Reconciliation is read-only unless the
user also authorizes specific edits. A request to identify checks does not imply
permission to categorize them, rename them, or create transaction rules.

## Select checks efficiently

- Establish the requested account, date range, and filters (for example,
  uncategorized checks). State any scope assumption. Do not silently restrict a
  request for all checks to uncategorized ones.
- Prefer the local Simplifi client and an existing encrypted snapshot for listing,
  filtering, and matching. Confirm snapshot age and coverage; fetch the missing
  interval or refresh when the task needs current completeness. Follow pagination
  and include the requested date boundaries. Inspect imported bank descriptions as
  well as displayed payees, which may already have been renamed.
- Build a private candidate inventory containing transaction ID, account identity,
  posted date, amount, check number as shown, category, payee, and existing memo.
  Keep account identifiers out of the user-facing summary unless needed to
  distinguish accounts. Check for duplicates before counting or totaling entries.

Read [references/access-and-edits.md](references/access-and-edits.md) for local
client capabilities, the observed browser workflow, or an authorized write.

## Match bank records and inspect images

Use the user's signed-in Schwab browser session for check records and images.
Use the available supported browser/computer-use tools and their documented APIs;
inspect the current UI before choosing controls. If authentication is needed, let
the user complete sign-in/MFA. Never collect passwords, MFA codes, session cookies,
or Schwab tokens in chat, scripts, or the skill.

1. Open the correct Schwab account's transaction history and cover the requested
   period. Locate each paid check by number, amount, and posted date; inspect all
   results/pages rather than assuming the first page is complete.
2. Match using account identity, check number, and the exact amount in cents.
   Dates corroborate identity: the handwritten issue date, invoice date, bank
   clearing date, and Simplifi posted date can differ. Report them distinctly.
   Compare a debit's magnitude to the positive check-face amount; preserve its
   original direction in the inventory. Do not use binary-float tolerances.
3. Treat masked or truncated check numbers as partial evidence. Preserve both
   representations and confirm the full number from the image or bank detail when
   available. Never equate suffixes alone. Duplicate amounts, reused numbers,
   different accounts, and multiple candidates remain ambiguous until resolved.
4. Open the bank detail/image and wait for the actual image to load. A modal shell
   or transaction description is not image evidence. Read the pay-to line, numeric
   amount, written amount when legible, check number, date, and relevant memo.
   Inspect the reverse only when needed for identification or discrepancy review.
5. Cross-check the image against the selected bank row and Simplifi candidate.
   Enlarge or revisit an unclear image. Transcribe names as observed; retain
   uncertain spelling rather than inventing a legal name from context. If amounts
   disagree or the payee is unreadable, report the discrepancy and do not guess.
6. Mark each candidate as confirmed, ambiguous, unreadable, image unavailable, or
   not found. After a failed image load, refresh the view once and revisit the
   selected record; if still unavailable, record the blocker and continue with
   other checks. Do not repeatedly retry the same blocked login or image.

If reconciling a supplied payment ledger, compare it separately to the bank
inventory. An invoice payment can clear later or combine multiple items. An
amount-only or approximate match is a candidate, not confirmation. Report missing,
duplicate, partial, combined, or unexplained amounts without inventing allocations.

## Report and optionally apply authorized edits

Provide a table of posted date, check number, debit amount, image payee, match
status, and any discrepancy or spelling uncertainty. Include a private evidence
reference where available. Give the count and exact total of confirmed checks,
plus unresolved counts; count each check once. Compare the final inventory to the
original candidate list so omissions are explicit. State what was actually changed.

Propose categories from the payment's purpose and the user's instructions. The
payee alone may not distinguish a medical bill from tuition, a donation from a
purchase, or one service from another. Preserve deliberate exceptions and leave
uncertain assignments for review. Do not create categories or broad rules merely
to complete reconciliation.

When edits are authorized, use only confirmed matches and the approved fields.
Refresh the exact transaction, preserve an encrypted original, detect intervening
changes, and verify the saved result. If appending the image payee, preserve the
check-number text and existing notes; do not append the same name twice. Preserve
amount, account, date, splits, and transfer links. After a timeout or uncertain
save, read back before retrying. Do not silently widen client write permissions.

## Keep private evidence out of source

Keep snapshots, check images, extracted names, account mappings, reconciliation
inventories, and backups in ignored private local storage. Encrypt retained raw
financial evidence using the supported local protection mechanism. If producing
a readable report, keep it ignored and disclose that it contains financial details.
Do not send check images to external OCR services without user authorization.
Avoid capturing unrelated balances, signatures, or routing/account numbers.

Commit only reusable instructions and synthetic examples or tests. Never embed
real payees, check numbers, amounts, account details, authentication material,
machine-specific paths, or previous task-specific category mappings in the skill.
