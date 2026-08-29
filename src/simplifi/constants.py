"""Shared constants for the Quicken Simplifi web client."""

from __future__ import annotations

# Public web application OAuth client credentials embedded in the Simplifi SPA.
# These identify the Quicken web app, not an individual user.
CLIENT_ID = "acme_web"
CLIENT_SECRET = "BCDCxXwdWYcj@bK6"

BASE_URL = "https://services.quicken.com"
REDIRECT_URI = "https://simplifi.quicken.com/login"

# Match headers the live web app sends so request routing stays consistent.
APP_RELEASE = "6.5.0"
APP_BUILD = "63580"

DEFAULT_PAGE_LIMIT = 1000
ACCESS_TOKEN_SKEW_SECONDS = 60
