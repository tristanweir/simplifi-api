---
name: simplifi-category-review
description: Review Quicken Simplifi transactions for inconsistent or suspicious categories, reconcile transfers and reimbursements, and apply authorized corrections with a resumable local review. Use for category reviews and numbered finding follow-ups; rule cleanup is a separate task.
---

# Simplifi category review

Use the local Simplifi client and encrypted review records to produce evidence-backed findings, resolve user decisions, and verify authorized corrections. Read repository `AGENTS.md` and [client workflow](references/client-workflow.md) before operating the client. Locate the checkout rather than assuming a machine path.

## Establish the review

Reuse the canonical structured review when one exists. Confirm dataset, date range, snapshot time, and prior decisions before downloading again. Preserve the immutable original, finding IDs, display numbers, completed history, and unresolved entries. Never infer current status solely from an old HTML report.

Prefer encrypted snapshots for broad analysis; refresh selected live records for reconciliation and before writes. Keep credentials, financial evidence, ownership context, and household preferences in ignored encrypted local records. Skill content and examples must remain generic. Another computer authenticates separately; DPAPI records are not cross-machine synchronization.

## Investigate candidates

- Compare categories for the same normalized payee and compare displayed payees with bank descriptions. Mixed categories are candidates, not proof of error: merchants sell different things, and payment apps represent unrelated recipients.
- Inspect date, account, amount/sign, original description, memo, category, splits, investment action, transfer links, recurring association, and Reports/Spending Plan exclusions. Distinguish transaction flags from account defaults.
- Handle investments separately from merchant spending. Trace sale proceeds and journals through accounts using exact amounts, dates, source activity codes, and reciprocal links. A sale followed by a journal credit can represent the same money rather than additional income. Preserve genuine investment actions.
- For reimbursements, search exact decimal amounts within a stated date window, inspect source/recipient and accepted offsets, and show all plausible matches. Amount alone does not establish an offset or transfer. Record differences for partial reimbursements. A confirmed offset can use the expense category to net its impact to zero; otherwise use the user's chosen treatment.
- For transfers, reconcile both sides and the account trail. Respect explicit budgeting conventions: some funding intentionally counts as an expense while destination deposits and later bills are excluded. Do not generalize a household convention to other accounts or users.

Present numbered findings with affected transactions, current category and exclusions, rationale, proposed treatment, and uncertainty. Keep original rationale separate from current values. Split heterogeneous groups by account/source while retaining the parent number. Ask only for information that materially determines treatment.

## Resolve decisions and corrections

A request to review or propose fixes does not authorize live edits. Existing explicit authorization can cover the selected correction; do not ask again for an already approved action.

Resolve groups and every exception to explicit transaction IDs. Reject unmatched exceptions and conflicting assignments across overlapping findings. Record correct entries as confirmations, including explanations requiring no live write. Keep deliberately unresolved items unresolved and honor instructions not to track a follow-up. Transaction decisions never implicitly create rules.

For authorized edits, use packaged plans where supported. Retain encrypted originals, exact previews, dataset binding, durable dispatch journals, amount signs, exclusion flags, and read-back verification. Recurring unlinking, transfer linking, and exclusion changes require authorization covering those changes.

If an operation is unsupported, inspect documented API gaps and available scoped writers before choosing a fallback. Do not weaken guards or invent a request contract. Explain necessary browser use, document the specific gap generically, and verify through API reads where possible.

After timeout or interruption, reconcile the existing journal through reads. Never automatically resend an uncertain write. Stop dependent writes when success or field preservation cannot be established, retaining evidence for recovery. Zero-dollar journals can be confirmed locally when evidence shows no correction is needed. Do not create counterparts or change live review flags merely to close a local finding.

## Close the loop

Persist verified results using library locking and atomic persistence. Preserve original evidence and history; close evidence gaps only when resolved. Explicitly regenerate HTML from structured records rather than patching serialized rows. HTML is readable financial data and belongs in the private review directory.

Report changed, already correct, confirmed, unresolved, and blocked counts separately when useful. State what changed, what verification established, and remaining decisions. Do not commit or publish financial artifacts as part of reviewing categories.
