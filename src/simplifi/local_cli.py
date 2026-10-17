"""Interactive local authentication; no secret command-line arguments."""

import argparse
import getpass
import json
from datetime import date
from pathlib import Path

from simplifi.client import Client
from simplifi.errors import SimplifiError
from simplifi.token_store import TokenStore


def write_receipt(result):
    from datetime import datetime, timezone

    receipt = Path(__file__).resolve().parents[2] / ".local" / "read-check.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps({**result, "checked_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )


def check(client, *, record=True):
    client.auth.verify()
    datasets = client.datasets()
    if not client.dataset_id:
        if len(datasets) != 1:
            raise SimplifiError("Expected one dataset; supply --dataset-id explicitly")
        client.http.set_dataset_id(str(datasets[0]["id"]))
    elif client.dataset_id not in {str(d["id"]) for d in datasets}:
        raise SimplifiError("Selected dataset is not available to this account")
    accounts = client.accounts()
    page = client.http.get("/transactions", params={"limit": 5})
    rows = page if isinstance(page, list) else page.get("resources")
    if not isinstance(rows, list):
        raise SimplifiError("Unexpected transaction response; verification incomplete")
    result = {
        "authenticated": True,
        "account_count": len(accounts),
        "transaction_sample_count": len(rows),
        "writes_enabled": False,
    }
    print(json.dumps(result, indent=2))
    if record:
        write_receipt(result)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Local Simplifi client with Windows encrypted auth"
    )
    parser.add_argument("command", choices=["login", "browser-login", "check", "export", "logout"])
    parser.add_argument("--browser", choices=["chrome", "msedge"], default="chrome")
    parser.add_argument("--dataset-id")
    parser.add_argument(
        "--resource",
        choices=["transactions", "accounts", "categories", "tags"],
        default="transactions",
    )
    parser.add_argument("--since", type=date.fromisoformat)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "browser-login":
            from simplifi.browser_login import browser_login

            browser_login(channel=args.browser, dataset_id=args.dataset_id)
            return
        if args.command == "logout":
            TokenStore().clear()
            print("Local tokens cleared. The server session is not revoked by this command.")
            return
        client = Client(dataset_id=args.dataset_id)
        if args.command == "login":
            email = input("Quicken email: ").strip()
            password = getpass.getpass("Quicken password (hidden): ")
            client.auth.login(email, password)
            del password
            check(client)
            print("Tokens encrypted for your Windows user; password was not saved.")
        elif args.command == "check":
            check(client)
        elif args.command == "export":
            if not args.output:
                parser.error("export requires --output; financial records are not printed")
            check(client)
            kwargs = (
                {"date_on_after": args.since.isoformat()}
                if args.since and args.resource == "transactions"
                else {}
            )
            data = getattr(client, args.resource)(**kwargs)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2)
            print(f"Exported {len(data)} records to the requested file.")
    except SimplifiError as error:
        print(str(error))
        raise SystemExit(1) from None
    except OSError:
        print("Local file operation failed; details suppressed.")
        raise SystemExit(1) from None
    except (ValueError, TypeError, KeyError):
        print("Unexpected API response; sensitive response details suppressed.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
