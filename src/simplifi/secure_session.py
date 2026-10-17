"""Restrict authenticated traffic to Quicken; never follow redirects."""

from urllib.parse import urlsplit

import requests

from simplifi.errors import SimplifiError


class SecureSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.trust_env = False  # No inherited .netrc credentials or proxy settings.

    def request(self, method, url, **kwargs):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "services.quicken.com"
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise SimplifiError("Blocked request outside the Quicken API origin")
        allowed = {"/oauth/authorize", "/oauth/token", "/transactions/earliest-date-on"}
        if method.upper() != "GET" and not (method.upper() == "POST" and parsed.path in allowed):
            raise SimplifiError("Transaction writes are disabled in this build")
        kwargs["allow_redirects"] = False
        kwargs["verify"] = True
        try:
            response = super().request(method, url, **kwargs)
        except requests.RequestException:
            raise SimplifiError("Quicken network/TLS request failed; details suppressed") from None
        if 300 <= response.status_code < 400 and parsed.path != "/oauth/authorize":
            raise SimplifiError("Unexpected redirect blocked")
        return response
