"""Packaged local review commands; only execute can mutate live transactions."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from simplifi.client import Client
from simplifi.errors import SimplifiError
from simplifi.review_import import build_legacy_import, import_legacy_review
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
    inspect_transaction_plan,
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
