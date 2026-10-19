"""Packaged local review commands; only execute can mutate live transactions."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from simplifi.client import Client
from simplifi.errors import SimplifiError
from simplifi.reconciliation import reconcile_transaction
from simplifi.relationship_plans import (
    execute_relationship_plan,
    inspect_relationship_plan,
    prepare_relationship_plan,
)
from simplifi.review_import import build_legacy_import, import_legacy_review
from simplifi.review_management import (
    attach_evidence,
    confirm_records,
    refresh_transactions,
    show_finding,
    unresolved_findings,
)
from simplifi.review_preferences import preferences_for
from simplifi.review_sync import reanalyze_review, set_review_preferences, sync_review
from simplifi.reviews import (
    download_review,
    load_review,
    record_decisions,
    reimbursement_candidates,
    render_review,
    review_summary,
)
from simplifi.transaction_plans import (
    execute_transaction_plan,
    import_transaction_receipt,
    inspect_transaction_plan,
    inspect_transaction_receipt,
    prepare_transaction_plan,
    transaction_plan_status,
)
from simplifi.transaction_rules import load_encrypted


def main(argv=None):
    parser = argparse.ArgumentParser(description="Encrypted Simplifi transaction reviews")
    parser.add_argument("--dataset-id")
    commands = parser.add_subparsers(dest="command", required=True)
    review_args = argparse.ArgumentParser(add_help=False)
    review_args.add_argument("--review", type=Path, required=True)
    download = commands.add_parser("download", parents=[review_args])
    download.add_argument("--start", type=date.fromisoformat, required=True)
    download.add_argument("--end", type=date.fromisoformat, required=True)
    commands.add_parser("summary", parents=[review_args])
    commands.add_parser("inventory", parents=[review_args])
    reconcile = commands.add_parser("reconcile", parents=[review_args])
    reconcile.add_argument("--transaction-id", required=True)
    reconcile.add_argument("--days", type=int, default=7)
    reconcile.add_argument("--max-hops", type=int, default=4)
    commands.add_parser("preferences", parents=[review_args])
    pref = commands.add_parser("preferences-set", parents=[review_args])
    pref.add_argument("--stdin", action="store_true", required=True)
    commands.add_parser("analyze", parents=[review_args])
    sync = commands.add_parser("sync", parents=[review_args])
    sync.add_argument(
        "--modified-after", help="Optional server delta cursor; ISO timestamp with timezone"
    )
    rel = commands.add_parser("relationship-plan", parents=[review_args])
    rel.add_argument("--action", choices=["link", "unlink", "unlink_recurring"], required=True)
    rel.add_argument("--transaction-id", action="append", required=True)
    rel.add_argument(
        "--targets-stdin",
        action="store_true",
        help="Read explicit unlink targets for both records as JSON from stdin",
    )
    rel.add_argument("--plan", type=Path, required=True)
    rel_inspect = commands.add_parser("relationship-inspect")
    rel_inspect.add_argument("--plan", type=Path, required=True)
    rel_execute = commands.add_parser("relationship-execute", parents=[review_args])
    rel_execute.add_argument("--plan", type=Path, required=True)
    rel_execute.add_argument("--confirm", required=True)
    rel_execute.add_argument("--journal", type=Path, required=True)
    show = commands.add_parser("show", parents=[review_args])
    show.add_argument("--number", type=int, required=True)
    commands.add_parser("unresolved", parents=[review_args])
    refresh = commands.add_parser("refresh", parents=[review_args])
    refresh.add_argument("--transaction-id", action="append", required=True)
    for name in ("confirm", "evidence"):
        cmd = commands.add_parser(name, parents=[review_args])
        cmd.add_argument("--number", type=int, required=True)
        cmd.add_argument("--transaction-id", action="append", required=True)
        cmd.add_argument(
            "--note-stdin",
            action="store_true",
            required=True,
            help="Read private explanation from stdin",
        )
        if name == "confirm":
            cmd.add_argument("--decision-id")
    receipt_inspect = commands.add_parser("receipt-inspect")
    receipt_inspect.add_argument("--receipt", type=Path, required=True)
    receipt_import = commands.add_parser("receipt-import", parents=[review_args])
    receipt_import.add_argument("--receipt", type=Path, required=True)
    receipt_import.add_argument("--confirm", required=True)
    receipt_import.add_argument("--number", type=int, required=True)
    decide = commands.add_parser("decide", parents=[review_args])
    selection = decide.add_mutually_exclusive_group(required=True)
    selection.add_argument("--decisions", type=Path)
    selection.add_argument(
        "--stdin", action="store_true", help="Read JSON decisions from stdin; save only encrypted"
    )
    render = commands.add_parser("render", parents=[review_args])
    render.add_argument("--output", type=Path, required=True)
    plan = commands.add_parser("plan", parents=[review_args])
    plan.add_argument("--plan", type=Path, required=True)
    plan.add_argument("--decision-id", action="append")
    execute = commands.add_parser("execute", parents=[review_args])
    execute.add_argument("--plan", type=Path, required=True)
    execute.add_argument("--confirm", required=True)
    execute.add_argument("--journal", type=Path, required=True)
    candidates = commands.add_parser("candidates", parents=[review_args])
    candidates.add_argument("--credit-id", required=True)
    candidates.add_argument("--days-before", type=int, default=45)
    candidates.add_argument("--days-after", type=int, default=3)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--plan", type=Path, required=True)
    status = commands.add_parser("status")
    status.add_argument("--journal", type=Path, required=True)
    for name in ("import-preview", "import-legacy"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--source", type=Path, required=True)
        cmd.add_argument("--start", type=date.fromisoformat)
        cmd.add_argument("--end", type=date.fromisoformat)
        if name == "import-legacy":
            cmd.add_argument("--destination", type=Path, required=True)
            cmd.add_argument("--confirm", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "import-preview":
            result = build_legacy_import(args.source, start=args.start, end=args.end)[1]
        elif args.command == "import-legacy":
            result = import_legacy_review(
                args.source,
                args.destination,
                confirmation=args.confirm,
                start=args.start,
                end=args.end,
            )
        elif args.command == "inspect":
            result = inspect_transaction_plan(args.plan)
        elif args.command == "status":
            result = transaction_plan_status(args.journal)
        elif args.command == "reconcile":
            result = reconcile_transaction(
                load_review(args.review),
                args.transaction_id,
                days=args.days,
                max_hops=args.max_hops,
            )
        elif args.command == "preferences":
            result = preferences_for(load_review(args.review)).to_wire()
        elif args.command == "preferences-set":
            result = set_review_preferences(args.review, json.load(sys.stdin))
        elif args.command == "analyze":
            result = reanalyze_review(args.review)
        elif args.command == "relationship-inspect":
            result = inspect_relationship_plan(args.plan)
        elif args.command == "show":
            result = show_finding(args.review, args.number)
        elif args.command == "unresolved":
            result = unresolved_findings(args.review)
        elif args.command == "confirm":
            result = confirm_records(
                args.review,
                args.number,
                args.transaction_id,
                note=sys.stdin.read(),
                decision_id=args.decision_id,
            )
        elif args.command == "evidence":
            result = attach_evidence(
                args.review, args.number, args.transaction_id, note=sys.stdin.read()
            )
        elif args.command == "receipt-inspect":
            result = inspect_transaction_receipt(args.receipt)
        elif args.command == "summary":
            result = review_summary(load_review(args.review))
        elif args.command == "inventory":
            review = load_review(args.review)
            result = {
                "findings": [f.to_wire() for f in review.findings],
                "categories": review.current.categories,
                "decisions": [d.to_wire() for d in review.decisions],
            }
        elif args.command == "render":
            result = render_review(args.review, args.output)
        elif args.command == "decide":
            if args.stdin:
                decisions = json.load(sys.stdin)
            elif args.decisions.suffix != ".dpapi":
                raise SimplifiError("Decision input must be encrypted .dpapi data")
            else:
                decisions = load_encrypted(args.decisions)
            result = record_decisions(args.review, decisions)
        elif args.command == "candidates":
            review = load_review(args.review)
            used = review.context.get("used_reimbursement_expense_ids", [])
            result = reimbursement_candidates(
                review.current,
                args.credit_id,
                days_before=args.days_before,
                days_after=args.days_after,
                used_expense_ids=used,
            )
        else:
            dataset = args.dataset_id
            if args.command != "download":
                dataset = dataset or load_review(args.review).current.dataset_id
            client = Client(dataset_id=dataset)
            if not client.dataset_id:
                client.resolve_dataset()
            if args.command == "download":
                result = download_review(client, args.review, start=args.start, end=args.end)
            elif args.command == "plan":
                result = prepare_transaction_plan(
                    client, args.review, plan_path=args.plan, decision_ids=args.decision_id
                )
            elif args.command == "relationship-plan":
                result = prepare_relationship_plan(
                    client,
                    args.review,
                    action=args.action,
                    transaction_ids=args.transaction_id,
                    plan_path=args.plan,
                    targets=json.load(sys.stdin) if args.targets_stdin else None,
                )
            elif args.command == "relationship-execute":
                result = execute_relationship_plan(
                    client,
                    args.plan,
                    confirmation=args.confirm,
                    journal_dir=args.journal,
                    review_dir=args.review,
                )
            elif args.command == "refresh":
                result = refresh_transactions(client, args.review, args.transaction_id)
            elif args.command == "sync":
                result = sync_review(client, args.review, modified_after=args.modified_after)
            elif args.command == "receipt-import":
                result = import_transaction_receipt(
                    client,
                    args.review,
                    args.receipt,
                    confirmation=args.confirm,
                    finding_number=args.number,
                )
            else:
                result = execute_transaction_plan(
                    client,
                    args.plan,
                    confirmation=args.confirm,
                    journal_dir=args.journal,
                    review_dir=args.review,
                )
        print(json.dumps(result, indent=2))
    except SimplifiError as error:
        parser.exit(1, str(error) + ". No automatic write retry.\n")
    except (OSError, ValueError, TypeError, KeyError):
        # Pydantic diagnostics can include the full sensitive input object.
        parser.exit(
            1,
            "Review operation stopped; inspect encrypted local evidence. "
            "No automatic write retry. Details suppressed.\n",
        )


if __name__ == "__main__":
    main()
