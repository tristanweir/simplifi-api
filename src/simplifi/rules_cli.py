"""Explicit rule listing, encrypted preview planning, and approved single updates."""

import argparse
import json
from pathlib import Path

from simplifi.client import Client
from simplifi.errors import SimplifiError
from simplifi.transaction_rules import apply_rule_update, prepare_rule_update, save_encrypted


def main():
    parser = argparse.ArgumentParser(description="Simplifi transaction rules")
    parser.add_argument("--dataset-id")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--encrypted-output", type=Path)
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
    args = parser.parse_args()
    try:
        client = Client(dataset_id=args.dataset_id)
        if not client.dataset_id:
            client.resolve_dataset()
        if args.command == "list":
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
