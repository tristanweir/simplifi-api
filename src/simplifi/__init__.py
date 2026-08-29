"""Unofficial read-only Quicken Simplifi (Personal) API client."""

from simplifi.client import Client
from simplifi.errors import AuthError, SimplifiError, SimplifiHTTPError

__all__ = ["Client", "AuthError", "SimplifiError", "SimplifiHTTPError"]
__version__ = "0.1.0"
