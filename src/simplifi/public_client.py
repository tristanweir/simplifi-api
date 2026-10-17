"""Discover the public web app's OAuth client metadata, without user credentials."""

import re
from urllib.parse import urljoin, urlsplit

import requests

from simplifi.errors import AuthError

APP_ORIGIN = "https://simplifi.quicken.com"


def public_client_secret():
    # This is public SPA metadata, not an account secret. Resolve it rather than
    # committing a credential-shaped value or embedding a stale web-app constant.
    try:
        with requests.Session() as session:
            session.trust_env = False

            def get(url):
                if urlsplit(url).netloc != "simplifi.quicken.com" or not url.startswith(
                    APP_ORIGIN + "/"
                ):
                    raise AuthError("Public app metadata origin rejected")
                response = session.get(url, timeout=30, allow_redirects=False)
                if response.status_code != 200:
                    raise AuthError("Could not retrieve public OAuth client metadata")
                return response.text

            html = get(APP_ORIGIN + "/")
            script = re.search(r'src=["\'](/main\.[A-Za-z0-9_-]+\.js)["\']', html)
            if not script:
                raise AuthError("Could not locate the public app bundle")
            bundle = get(urljoin(APP_ORIGIN, script[1]))
            match = re.search(
                r'"client_id"\s*:\s*"acme_web"\s*,\s*"client_secret"\s*:\s*"([^"\\]+)"', bundle
            )
            if not match:
                raise AuthError("Public OAuth client metadata format changed")
            return match[1]
    except (requests.RequestException, ValueError):
        raise AuthError("Public OAuth client metadata lookup failed; details suppressed") from None
