#!/usr/bin/env python3
"""Parse a browser HAR export and list unique services.quicken.com API calls.

Usage:
  1. Sign in to https://simplifi.quicken.com in Chrome/Firefox.
  2. Open DevTools → Network, enable "Preserve log".
  3. Visit each Personal tab (Dashboard, Spending Plan, Bills & Income, Goals,
     Planning Tools, Investments, Watchlist, Reports, Transactions, Settings).
  4. Export HAR (right-click the request list → Save all as HAR).
  5. Run:
       python scripts/parse_har.py path/to/export.har

Do not commit HAR files — they contain auth tokens and personal finance data.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("har", type=Path, help="Path to a .har file")
    parser.add_argument(
        "--host",
        default="services.quicken.com",
        help="Hostname to filter (default: services.quicken.com)",
    )
    args = parser.parse_args()

    payload = json.loads(args.har.read_text(encoding="utf-8"))
    entries = payload.get("log", {}).get("entries", [])
    counts: Counter[str] = Counter()
    samples: dict[str, dict] = {}

    for entry in entries:
        request = entry.get("request") or {}
        url = request.get("url") or ""
        parsed = urlparse(url)
        if parsed.hostname != args.host:
            continue
        method = (request.get("method") or "GET").upper()
        path = parsed.path or "/"
        key = f"{method} {path}"
        counts[key] += 1
        if key not in samples:
            headers = {
                h.get("name", "").lower(): h.get("value")
                for h in request.get("headers") or []
            }
            samples[key] = {
                "query": parsed.query,
                "has_qcs_dataset_id": "qcs-dataset-id" in headers,
                "app_client_id": headers.get("app-client-id"),
            }

    if not counts:
        print(f"No requests found for host={args.host}", file=sys.stderr)
        return 1

    print(f"# Unique {args.host} endpoints ({len(counts)})\n")
    print("| Count | Method | Path | Dataset header |")
    print("|------:|--------|------|----------------|")
    for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        method, path = key.split(" ", 1)
        sample = samples[key]
        print(
            f"| {count} | `{method}` | `{path}` | "
            f"{'yes' if sample['has_qcs_dataset_id'] else 'no'} |"
        )

    print("\n# Suggested Client method mapping\n")
    for key in sorted(counts):
        method, path = key.split(" ", 1)
        slug = path.strip("/").replace("/", "_").replace("-", "_") or "root"
        print(f"- `{method} {path}` → `Client` / `Resources.{slug}()`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
