"""Shared constants for the Quicken Simplifi web client."""

from __future__ import annotations

# Public web application OAuth client credentials embedded in the Simplifi SPA.
# These identify the Quicken web app, not an individual user.
CLIENT_ID = "acme_web"
# Resolved from the public Quicken app at runtime when exchanging/refreshing tokens.
CLIENT_SECRET = ""

BASE_URL = "https://services.quicken.com"
REDIRECT_URI = "https://simplifi.quicken.com/login"

# Match headers the live web app sends so request routing stays consistent.
# Verified against the production SPA on 2026-10-17.
APP_RELEASE = "6.41.1"
APP_BUILD = "70343"

DEFAULT_PAGE_LIMIT = 1000
ACCESS_TOKEN_SKEW_SECONDS = 60
