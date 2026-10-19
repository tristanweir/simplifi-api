"""Rule reads, encrypted change planning, and explicitly approved resumable execution."""

import argparse
import json
import sys
from pathlib import Path

from simplifi.client import Client
from simplifi.errors import SimplifiError
from simplifi.rule_creation import (
    execute_rule_creation,
    inspect_rule_creation,
    prepare_rule_creation,
)
from simplifi.rule_plans import (
    apply_rule_deletion,
    change_plan_status,
    execute_change_plan,
    inspect_change_plan,
    prepare_change_plan,
    prepare_rule_deletion,
)
from simplifi.transaction_rules import (
    apply_rule_update,
    load_encrypted,
    prepare_rule_update,
    save_encrypted,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Simplifi transaction rules")
    parser.add_argument("--dataset-id")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-plan", help="Preview a future-only rule creation")
    source = create.add_mutually_exclusive_group(required=True)
    source.add_argument("--stdin", action="store_true")
    source.add_argument("--changes", type=Path, help="Encrypted .dpapi editable fields")
    create.add_argument("--plan", type=Path, required=True)
    create_review = commands.add_parser("create-review")
    create_review.add_argument("--plan", type=Path, required=True)
    create_execute = commands.add_parser("create-execute")
    create_execute.add_argument("--plan", type=Path, required=True)
    create_execute.add_argument("--confirm", required=True)
    create_execute.add_argument("--journal", type=Path, required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--encrypted-output", type=Path)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--rule-id", required=True)
    plan = commands.add_parser("plan", help="Prepare an encrypted, future-only change plan")
    selection = plan.add_mutually_exclusive_group(required=True)
    selection.add_argument("--operations", type=Path, help="JSON list or encrypted .dpapi list")
    selection.add_argument("--delete-rule-id", help="Prepare a single deletion")
    plan.add_argument("--plan", type=Path, required=True)
    review = commands.add_parser("review", help="Review an encrypted plan without logging in")
    review.add_argument("--plan", type=Path, required=True)
    for name in ("execute", "delete"):
        execute = commands.add_parser(name, help="Execute or safely resume a reviewed plan")
        execute.add_argument("--plan", type=Path, required=True)
        execute.add_argument("--confirm", required=True)
        execute.add_argument("--journal", type=Path, required=True)
    status = commands.add_parser("status", help="Read local encrypted execution status")
    status.add_argument("--journal", type=Path, required=True)
    preview = commands.add_parser("preview")
    preview.add_argument("--rule-id", required=True)
    preview.add_argument(
        "--changes", type=Path, required=True, help="JSON object using API field names"
    )
    preview.add_argument("--plan", type=Path, required=True, help="New .dpapi plan filename")
    preview.add_argument(
        "--apply-existing",
        action="store_true",
        help="Preview a historical application; does not update anything",
    )
    update = commands.add_parser("update")
    update.add_argument("--plan", type=Path, required=True)
    update.add_argument("--confirm", required=True, help="Exact confirmation digest from preview")
    update.add_argument("--backup", type=Path, required=True, help="New .dpapi backup filename")
    update.add_argument(
        "--apply-existing",
        action="store_true",
        help="Explicitly authorize the historical application in the approved plan",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "create-review":
            print(json.dumps(inspect_rule_creation(args.plan), indent=2))
            return
        if args.command in {"review", "status"}:
            result = (
                inspect_change_plan(args.plan)
                if args.command == "review"
                else change_plan_status(args.journal)
            )
            print(json.dumps(result, indent=2))
            return
        client = Client(dataset_id=args.dataset_id)
        if not client.dataset_id:
            client.resolve_dataset()
        if args.command == "inspect":
            print(json.dumps(client.transaction_rule(args.rule_id), indent=2))
        elif args.command == "create-plan":
            if args.stdin:
                changes = json.load(sys.stdin)
            elif args.changes.suffix == ".dpapi":
                changes = load_encrypted(args.changes)
            else:
                raise SimplifiError("Creation input requires encrypted .dpapi or stdin")
            print(json.dumps(prepare_rule_creation(client, changes, plan_path=args.plan), indent=2))
        elif args.command == "create-execute":
            print(
                json.dumps(
                    execute_rule_creation(
                        client, args.plan, confirmation=args.confirm, journal_dir=args.journal
                    ),
                    indent=2,
                )
            )
        elif args.command == "plan":
            if args.delete_rule_id:
                result = prepare_rule_deletion(client, args.delete_rule_id, plan_path=args.plan)
            else:
                operations = (
                    load_encrypted(args.operations)
                    if args.operations.suffix == ".dpapi"
                    else json.loads(args.operations.read_text(encoding="utf-8"))
                )
                result = prepare_change_plan(client, operations, plan_path=args.plan)
            print(json.dumps(result, indent=2))
        elif args.command in {"execute", "delete"}:
            apply = apply_rule_deletion if args.command == "delete" else execute_change_plan
            print(
                json.dumps(
                    apply(client, args.plan, confirmation=args.confirm, journal_dir=args.journal),
                    indent=2,
                )
            )
        elif args.command == "list":
            rules = client.transaction_rules()
            if args.encrypted_output:
                save_encrypted(
                    args.encrypted_output, {"dataset_id": client.dataset_id, "rules": rules}
                )
                print(json.dumps({"rule_count": len(rules), "encrypted_snapshot_saved": True}))
            else:
                print(json.dumps(rules, indent=2))
        elif args.command == "preview":
            changes = json.loads(args.changes.read_text(encoding="utf-8"))
            result = prepare_rule_update(
                client,
                client.transaction_rule(args.rule_id),
                changes,
                plan_path=args.plan,
                apply_to_existing=args.apply_existing,
            )
            print(json.dumps(result, indent=2))
        elif args.command == "update":
            apply_rule_update(
                client,
                args.plan,
                confirmation=args.confirm,
                backup_path=args.backup,
                allow_existing=args.apply_existing,
            )
            print(
                json.dumps(
                    {
                        "rule_verified": True,
                        "historical_application_authorized": args.apply_existing,
                        "historical_application_completion_verified": False,
                    }
                )
            )
    except SimplifiError as error:
        print(str(error))
        raise SystemExit(1) from None
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print("Invalid local rule input or API response; details suppressed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
