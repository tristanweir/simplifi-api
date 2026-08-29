"""Token persistence for access + refresh tokens."""

from __future__ import annotations

import json
import os
from pathlib import Path

from simplifi.models import TokenSet


def default_token_path() -> Path:
    override = os.environ.get("SIMPLIFI_TOKEN_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".simplifi" / "tokens.json"


class TokenStore:
    """JSON file store under ~/.simplifi (or SIMPLIFI_TOKEN_PATH)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_token_path()

    def load(self) -> TokenSet | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not data.get("access_token") or not data.get("refresh_token"):
            return None
        return TokenSet.model_validate(data)

    def save(self, tokens: TokenSet) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = tokens.model_dump(mode="json")
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
