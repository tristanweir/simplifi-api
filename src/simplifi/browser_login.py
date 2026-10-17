"""User-operated browser login; capture only Quicken's successful token response."""

from __future__ import annotations

import os
import time
from urllib.parse import urlsplit

from simplifi.auth import AuthSession
from simplifi.client import Client
from simplifi.errors import AuthError
from simplifi.models import TokenSet
from simplifi.token_store import TokenStore

APP_URL = "https://simplifi.quicken.com/"


class MemoryTokenStore:
    """Hold candidate tokens in memory until independent API reads succeed."""

    def load(self):
        return None

    def save(self, tokens):
        pass

    def clear(self):
        pass


def is_token_response(response) -> bool:
    url = urlsplit(response.url)
    return (
        url.scheme == "https"
        and url.netloc == "services.quicken.com"
        and url.path == "/oauth/token"
        and not url.query
        and not url.fragment
        and response.request.method == "POST"
        and response.status == 200
    )


def parse_browser_tokens(payload) -> TokenSet:
    if not isinstance(payload, dict):
        raise AuthError("Unexpected browser token response; details suppressed")
    access = payload.get("accessToken") or payload.get("access_token")
    refresh = payload.get("refreshToken") or payload.get("refresh_token")
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
        raise AuthError("Browser token response lacks access/refresh tokens")
    # Reuse upstream expiry parsing; do not persist or import any extra response fields.
    auth = AuthSession(token_store=MemoryTokenStore())
    return auth._parse_token_payload(payload)


def capture_tokens(*, channel="chrome", timeout_seconds=600) -> TokenSet:
    if any(os.environ.get(key) for key in ("DEBUG", "PWDEBUG", "SSLKEYLOGFILE")):
        raise AuthError("Disable DEBUG, PWDEBUG, and SSLKEYLOGFILE before browser sign-in")
    try:
        from playwright.sync_api import Error as BrowserError
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise AuthError("Install the browser extra: pip install -e '.[browser]'") from None

    captured = []
    parse_failed = []

    def on_response(response):
        # Never access request bodies, form fields, cookies, or unrelated response bodies.
        if captured or not is_token_response(response):
            return
        try:
            captured.append(parse_browser_tokens(response.json()))
        except (ValueError, TypeError, AuthError, BrowserError):
            parse_failed.append(True)

    print(
        "Opening a separate browser window. Sign in and complete MFA on Quicken's website.",
        flush=True,
    )
    print(
        "No password entry is automated. No HAR, screenshots, traces, or browser storage exports are saved.",
        flush=True,
    )
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=channel, headless=False)
            try:
                context = browser.new_context(accept_downloads=False)
                context.on("response", on_response)
                page = context.new_page()
                page.goto(APP_URL, wait_until="domcontentloaded", timeout=60000)
                deadline = time.monotonic() + timeout_seconds
                while not captured:
                    if parse_failed:
                        raise AuthError(
                            "Quicken returned an unsupported token response; details suppressed"
                        )
                    if not browser.is_connected() or not context.pages:
                        raise AuthError("Browser was closed before sign-in completed")
                    if time.monotonic() >= deadline:
                        raise AuthError("Browser sign-in timed out; no tokens saved")
                    # Pump browser events without reading the page or interacting with login controls.
                    context.pages[0].wait_for_timeout(250)
                context.remove_listener("response", on_response)
                return captured[0]
            finally:
                browser.close()
    except BrowserError:
        raise AuthError(
            "Browser launch/navigation failed; details suppressed. Try the other browser channel."
        ) from None


def verify_and_save(tokens, *, dataset_id=None, store=None):
    from simplifi.local_cli import check, write_receipt

    client = Client(dataset_id=dataset_id, token_store=MemoryTokenStore())
    client.auth.adopt_tokens(tokens)
    try:
        result = check(client, record=False)
        # Verification can rotate tokens. Save the latest, verified set only.
        (store or TokenStore()).save(client.auth.tokens)
        write_receipt(result)
        print(
            "Browser sign-in verified. Tokens saved with Windows user-bound encryption.", flush=True
        )
        return result
    finally:
        client.session.close()


def browser_login(*, channel="chrome", dataset_id=None):
    tokens = capture_tokens(channel=channel)
    print(
        "Quicken token exchange captured. Browser closed; verifying direct API reads.", flush=True
    )
    return verify_and_save(tokens, dataset_id=dataset_id)
