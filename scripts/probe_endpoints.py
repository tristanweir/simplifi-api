#!/usr/bin/env python3
"""Probe known Personal Simplifi endpoints with a stored token.

Requires a prior `simplifi login`. Useful for confirming which paths exist for
your tenant after Quicken ships SPA changes.

  python scripts/probe_endpoints.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Allow running from a checkout without install.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from simplifi.client import Client  # noqa: E402
from simplifi.errors import SimplifiHTTPError  # noqa: E402

CANDIDATES = [
    ("GET", "/datasets", False),
    ("GET", "/accounts", True),
    ("GET", "/transactions", True),
    ("GET", "/categories", True),
    ("GET", "/tags", True),
    ("GET", "/scheduled-transactions", True),
    ("GET", "/goals", True),
    ("GET", "/savings-goals", True),
    ("GET", "/spending-watchlists", True),
    ("GET", "/watchlists", True),
    ("GET", "/v2/investments/securities", True),
    ("GET", "/investments/holdings", True),
    ("GET", "/free-to-spend", True),
    ("GET", "/planned-spending", True),
    ("GET", "/preferences", True),
    ("GET", "/filters", True),
    ("GET", "/alert-rules", True),
    ("GET", "/alerts", True),
    ("GET", "/institution-logins", True),
    ("GET", "/report-configurations", True),
    ("GET", "/feature-flags", True),
    ("GET", "/entitlements", True),
    ("GET", "/transfers/balance", True),
    ("GET", "/userprofiles/me", False),
]


def main() -> int:
    client = Client(dataset_id=os.environ.get("SIMPLIFI_DATASET_ID") or None)
    try:
        client.auth.ensure_access_token()
    except Exception as exc:  # noqa: BLE001
        print(f"Auth required: {exc}", file=sys.stderr)
        return 1
    if not client.dataset_id:
        client.resolve_dataset()

    results = []
    for method, path, require_dataset in CANDIDATES:
        try:
            if method == "GET":
                payload = client.http.get(
                    path, params={"limit": 1}, require_dataset=require_dataset
                )
            else:
                payload = None
            count = None
            if isinstance(payload, dict) and isinstance(payload.get("resources"), list):
                count = len(payload["resources"])
            elif isinstance(payload, list):
                count = len(payload)
            results.append(
                {"method": method, "path": path, "status": "ok", "sample_count": count}
            )
            print(f"OK   {method} {path}")
        except SimplifiHTTPError as exc:
            results.append(
                {
                    "method": method,
                    "path": path,
                    "status": f"http_{exc.status_code}",
                    "body": exc.body[:200],
                }
            )
            print(f"FAIL {method} {path} -> {exc.status_code}")
        except Exception as exc:  # noqa: BLE001
            results.append(
                {"method": method, "path": path, "status": "error", "error": str(exc)}
            )
            print(f"ERR  {method} {path} -> {exc}")

    out = Path("exports") / "endpoint-probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
