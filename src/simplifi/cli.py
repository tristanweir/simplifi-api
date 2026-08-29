"""Command-line interface for quicken-simplifi-api."""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import click

from simplifi.client import Client


def _client_from_env() -> Client:
    return Client(
        dataset_id=os.environ.get("SIMPLIFI_DATASET_ID") or None,
        threat_metrix_session_id=os.environ.get("SIMPLIFI_THREAT_METRIX_SESSION_ID"),
    )


def _write_output(data: Any, *, filename: str | None, fmt: str) -> None:
    if filename:
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "csv":
            rows = data if isinstance(data, list) else [data]
            if not rows:
                path.write_text("", encoding="utf-8")
                return
            flat = [_flatten(r) if isinstance(r, dict) else {"value": r} for r in rows]
            # pydantic models
            flat = [
                _flatten(r.model_dump()) if hasattr(r, "model_dump") else r for r in flat
            ]
            fieldnames: list[str] = []
            for row in flat:
                for key in row:
                    if key not in fieldnames:
                        fieldnames.append(key)
            with path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(flat)
        else:
            payload = data.model_dump() if hasattr(data, "model_dump") else data
            if isinstance(payload, list):
                payload = [
                    item.model_dump() if hasattr(item, "model_dump") else item
                    for item in payload
                ]
            path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        click.echo(f"Wrote {path}")
    else:
        payload = data.model_dump() if hasattr(data, "model_dump") else data
        if isinstance(payload, list):
            payload = [
                item.model_dump() if hasattr(item, "model_dump") else item for item in payload
            ]
        click.echo(json.dumps(payload, indent=2, default=str))


def _flatten(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        full = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, full))
        elif isinstance(value, list):
            out[full] = json.dumps(value, default=str)
        else:
            out[full] = value
    return out


@click.group()
@click.version_option(package_name="quicken-simplifi-api")
def main() -> None:
    """Unofficial read-only Quicken Simplifi (Personal) API."""


@main.command("login")
@click.option("--email", envvar="SIMPLIFI_EMAIL", prompt=True)
@click.option("--password", envvar="SIMPLIFI_PASSWORD", prompt=True, hide_input=True)
@click.option("--mfa-code", default=None, help="MFA code if already received")
def login_cmd(email: str, password: str, mfa_code: str | None) -> None:
    """Authenticate once and store access + refresh tokens locally."""
    client = _client_from_env()
    client.login(email, password, mfa_code=mfa_code, mfa_prompt=mfa_code is None)
    click.echo(f"Logged in. dataset_id={client.dataset_id}")


@main.command("logout")
def logout_cmd() -> None:
    """Clear stored tokens."""
    _client_from_env().logout()
    click.echo("Logged out.")


@main.command("whoami")
def whoami_cmd() -> None:
    """Show the authenticated profile and dataset id."""
    client = _client_from_env()
    if not client.http.dataset_id:
        client.resolve_dataset()
    profile = client.profile()
    click.echo(
        json.dumps(
            {"dataset_id": client.dataset_id, "profile": profile},
            indent=2,
            default=str,
        )
    )


@main.command("export")
@click.argument(
    "resource",
    type=click.Choice(
        [
            "accounts",
            "transactions",
            "categories",
            "tags",
            "scheduled-transactions",
            "goals",
            "watchlists",
            "securities",
            "holdings",
            "institution-logins",
            "report-configurations",
            "spending-plan",
            "spending-by-category",
            "monthly-burn",
            "income-expense",
            "monthly-summary",
            "bills-income",
            "dashboard",
        ]
    ),
)
@click.option("--filename", "-o", default=None, help="Output file path")
@click.option("--format", "fmt", type=click.Choice(["json", "csv"]), default="json")
@click.option("--month", default=None, help="YYYY-MM for month-scoped views")
@click.option("--from-date", "from_date", default=None, help="YYYY-MM-DD")
@click.option("--to-date", "to_date", default=None, help="YYYY-MM-DD")
@click.option("--date-on-after", default=None, help="Incremental transactions filter")
def export_cmd(
    resource: str,
    filename: str | None,
    fmt: str,
    month: str | None,
    from_date: str | None,
    to_date: str | None,
    date_on_after: str | None,
) -> None:
    """Export a resource or tab-shaped view."""
    client = _client_from_env()
    if not client.http.dataset_id:
        client.resolve_dataset()

    today = date.today()
    if not month:
        month = today.strftime("%Y-%m")
    if not to_date:
        to_date = today.isoformat()
    if not from_date:
        from_date = (today.replace(day=1) - timedelta(days=365)).isoformat()

    data: Any
    if resource == "accounts":
        data = client.accounts()
    elif resource == "transactions":
        data = client.transactions(date_on_after=date_on_after)
    elif resource == "categories":
        data = client.categories()
    elif resource == "tags":
        data = client.tags()
    elif resource == "scheduled-transactions":
        data = client.scheduled_transactions()
    elif resource == "goals":
        data = client.goals()
    elif resource == "watchlists":
        data = client.spending_watchlists()
    elif resource == "securities":
        data = client.securities()
    elif resource == "holdings":
        data = client.holdings()
    elif resource == "institution-logins":
        data = client.institution_logins()
    elif resource == "report-configurations":
        data = client.report_configurations()
    elif resource == "spending-plan":
        data = client.views.spending_plan_summary(month=month)
    elif resource == "spending-by-category":
        data = client.views.spending_by_category(from_date=from_date, to_date=to_date)
    elif resource == "monthly-burn":
        data = client.views.monthly_burn(from_date=from_date, to_date=to_date)
    elif resource == "income-expense":
        data = client.views.income_vs_expense(from_date=from_date, to_date=to_date)
    elif resource == "monthly-summary":
        data = client.views.monthly_summary(month=month)
    elif resource == "bills-income":
        data = client.views.bills_income_upcoming(from_date=from_date, to_date=to_date)
    elif resource == "dashboard":
        data = client.views.dashboard_snapshot()
    else:
        click.echo(f"Unknown resource: {resource}", err=True)
        sys.exit(1)

    _write_output(data, filename=filename, fmt=fmt)


if __name__ == "__main__":
    main()
