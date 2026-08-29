"""Typed errors for the Simplifi client."""

from __future__ import annotations


class SimplifiError(Exception):
    """Base error for this package."""


class AuthError(SimplifiError):
    """Authentication or token refresh failed."""


class SimplifiHTTPError(SimplifiError):
    """Unexpected HTTP response from services.quicken.com."""

    def __init__(self, status_code: int, url: str, body: str) -> None:
        self.status_code = status_code
        self.url = url
        self.body = body
        super().__init__(f"Simplifi HTTP {status_code} for {url}: {body[:500]}")


class ReadOnlyViolation(SimplifiError):
    """Raised if a mutating HTTP method is attempted."""
