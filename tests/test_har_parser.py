from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.fixtures.sample_har import SAMPLE_HAR


def test_parse_har_lists_simplifi_endpoints(tmp_path: Path):
    har_path = tmp_path / "sample.har"
    har_path.write_text(json.dumps(SAMPLE_HAR), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "parse_har.py"
    result = subprocess.run(
        [sys.executable, str(script), str(har_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "/accounts" in result.stdout
    assert "/scheduled-transactions" in result.stdout
    assert "example.com" not in result.stdout
