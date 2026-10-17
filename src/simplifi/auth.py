"""OAuth login, MFA, and refresh-token handling."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from simplifi.constants import (
    ACCESS_TOKEN_SKEW_SECONDS,
    APP_BUILD,
    APP_RELEASE,
    BASE_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    REDIRECT_URI,
)
from simplifi.errors import AuthError
from simplifi.models import TokenSet
from simplifi.public_client import public_client_secret
from simplifi.secure_session import SecureSession
from simplifi.token_store import TokenStore

logger = logging.getLogger("simplifi.auth")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


class AuthSession:
    """Handles Quicken Simplifi OAuth for the public web client."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        token_store: TokenStore | None = None,
        base_url: str = BASE_URL,
        client_id: str = CLIENT_ID,
        client_secret: str = CLIENT_SECRET,
        redirect_uri: str = REDIRECT_URI,
        threat_metrix_session_id: str | None = None,
    ) -> None:
        self.session = session or SecureSession()
        self.token_store = token_store or TokenStore()
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.threat_metrix_session_id = threat_metrix_session_id
        self._tokens: TokenSet | None = self.token_store.load()
        self._session_state: str | None = None

    @property
    def tokens(self) -> TokenSet | None:
        return self._tokens

    def adopt_tokens(self, tokens: TokenSet) -> None:
        """Use tokens in memory; caller must verify them before persisting."""
        self._tokens = tokens

    def _auth_headers(self) -> dict[str, str]:
        headers = {
            "content-type": "application/json;charset=UTF-8",
            "accept": "application/json, text/plain, */*",
            "app-client-id": self.client_id,
            "app-release": APP_RELEASE,
            "app-build": APP_BUILD,
        }
        if self._session_state:
            headers["qcs-session-state"] = self._session_state
            headers["qcs-session-state-provided"] = "true"
        return headers

    @staticmethod
    def _failure(stage: str, response: requests.Response) -> AuthError:
        # Only accept documented-shape codes, never server messages or arbitrary values.
        codes = set()
        try:
            payload = response.json()
            candidates = [payload.get("code")]
            errors = payload.get("errors")
            if isinstance(errors, list):
                candidates.extend(item.get("code") for item in errors if isinstance(item, dict))
            codes = {
                value
                for value in candidates
                if isinstance(value, str) and re.fullmatch(r"QCS-[0-9]{4}(?:-[0-9]{1,3})?", value)
            }
        except (ValueError, AttributeError, TypeError):
            pass
        suffix = f"; code={','.join(sorted(codes))}" if codes else ""
        return AuthError(
            f"{stage} failed (HTTP {response.status_code}{suffix}); response suppressed"
        )

    def _check_session_state(self, response: requests.Response) -> None:
        echoed = response.headers.get("qcs-session-state")
        if echoed is not None and echoed != self._session_state:
            raise AuthError("Authorize failed: session-state mismatch; response suppressed")

    def ensure_access_token(self) -> str:
        tokens = self._tokens or self.token_store.load()
        if tokens and not self._access_expired(tokens):
            self._tokens = tokens
            return tokens.access_token

        if tokens and tokens.refresh_token:
            try:
                refreshed = self.refresh(tokens.refresh_token)
                self._tokens = refreshed
                self.token_store.save(refreshed)
                return refreshed.access_token
            except AuthError as exc:
                logger.warning("Token refresh failed: %s", exc)

        raise AuthError("No valid access token. Run `simplifi login` or call Client.login() first.")

    def _access_expired(self, tokens: TokenSet) -> bool:
        expires = _parse_iso(tokens.access_token_expires_at)
        if expires is None:
            return True
        return expires <= _utc_now() + timedelta(seconds=ACCESS_TOKEN_SKEW_SECONDS)

    def login(
        self,
        email: str,
        password: str,
        *,
        mfa_code: str | None = None,
        mfa_prompt: bool = True,
    ) -> TokenSet:
        threat_id = self.threat_metrix_session_id or str(uuid.uuid4())
        self._session_state = str(uuid.uuid4())
        body: dict[str, Any] = {
            "clientId": self.client_id,
            "username": email,
            "password": password,
            "redirectUri": self.redirect_uri,
            "responseType": "code",
            "mfaChannel": None,
            "mfaCode": None,
            "mfaId": None,
            "threatMetrixRequestId": None,
            "threatMetrixSessionId": threat_id,
        }
        response = self.session.post(
            f"{self.base_url}/oauth/authorize",
            json=body,
            headers={**self._auth_headers(), "tm-session-id": threat_id},
            allow_redirects=False,
            timeout=60,
        )

        if response.status_code == 202:
            self._check_session_state(response)
            payload = response.json()
            channel = payload.get("mfaChannel") or "EMAIL"
            mfa_id = payload.get("mfaId")
            code = mfa_code
            if code is None and mfa_prompt:
                print(f"MFA required via {channel}. Check your email/phone.")
                import getpass

                code = getpass.getpass("MFA Code (hidden): ").strip()
            if not code:
                raise AuthError("MFA code required but not provided")
            body["mfaChannel"] = channel
            body["mfaCode"] = code
            body["mfaId"] = mfa_id
            request_id = payload.get("threatMetrixRequestId")
            body["threatMetrixRequestId"] = request_id
            mfa_headers = {**self._auth_headers(), "tm-session-id": threat_id}
            if isinstance(request_id, str) and request_id:
                mfa_headers["tm-request-id"] = request_id
            response = self.session.post(
                f"{self.base_url}/oauth/authorize",
                json=body,
                headers=mfa_headers,
                allow_redirects=False,
                timeout=60,
            )

        code = self._extract_auth_code(response)
        tokens = self.exchange_code(code)
        self._tokens = tokens
        self.token_store.save(tokens)
        return tokens

    def _extract_auth_code(self, response: requests.Response) -> str:
        if response.status_code >= 400:
            raise self._failure("Authorize", response)
        self._check_session_state(response)
        location = response.headers.get("Location") or response.headers.get("location")
        if location:
            parsed = urlparse(location)
            qs = parse_qs(parsed.query)
            if qs.get("code"):
                return qs["code"][0]

        # Older/alternate responses return JSON with code/status.
        try:
            payload = response.json()
        except ValueError as exc:
            raise AuthError(
                f"Authorize failed (HTTP {response.status_code}); response suppressed"
            ) from exc

        status = payload.get("status")
        if status and status not in {"User passed MFA", "success", None} and "MFA" in str(status):
            raise AuthError("Authorize MFA incomplete; response suppressed")
        code = payload.get("code")
        if code:
            return str(code)
        raise AuthError(f"Authorize did not return a code (HTTP {response.status_code})")

    def exchange_code(self, code: str) -> TokenSet:
        if not self.client_secret:
            self.client_secret = public_client_secret()
        response = self.session.post(
            f"{self.base_url}/oauth/token",
            json={
                "grantType": "authorization_code",
                "clientId": self.client_id,
                "clientSecret": self.client_secret,
                "code": code,
                "redirectUri": self.redirect_uri,
            },
            headers=self._auth_headers(),
            timeout=60,
        )
        if response.status_code != 200:
            raise self._failure("Token exchange", response)
        return self._parse_token_payload(response.json())

    def refresh(self, refresh_token: str) -> TokenSet:
        if not self.client_secret:
            self.client_secret = public_client_secret()
        response = self.session.post(
            f"{self.base_url}/oauth/token",
            json={
                "grantType": "refreshToken",
                "responseType": "token",
                "redirectUri": self.redirect_uri,
                "clientId": self.client_id,
                "clientSecret": self.client_secret,
                "refreshToken": refresh_token,
            },
            headers=self._auth_headers(),
            timeout=60,
        )
        if response.status_code != 200:
            raise self._failure("Token refresh", response)
        return self._parse_token_payload(response.json())

    def refresh_access_token(self) -> str:
        if not self._tokens or not self._tokens.refresh_token:
            raise AuthError("Sign in locally again; no refresh token is available")
        tokens = self.refresh(self._tokens.refresh_token)
        self.token_store.save(tokens)
        self._tokens = tokens
        return tokens.access_token

    def use_access_token(self, access_token: str, *, refresh_token: str = "") -> TokenSet:
        tokens = TokenSet(
            access_token=access_token,
            refresh_token=refresh_token or "manual",
            access_token_expires_at=(_utc_now() + timedelta(hours=1)).isoformat(),
        )
        self._tokens = tokens
        if refresh_token:
            self.token_store.save(tokens)
        return tokens

    def verify(self, access_token: str | None = None) -> dict[str, Any]:
        token = access_token or self.ensure_access_token()
        response = self.session.get(
            f"{self.base_url}/userprofiles/me",
            headers={**self._auth_headers(), "Authorization": f"Bearer {token}"},
            timeout=60,
        )
        if response.status_code == 401 and access_token is None:
            token = self.refresh_access_token()
            response = self.session.get(
                f"{self.base_url}/userprofiles/me",
                headers={**self._auth_headers(), "Authorization": f"Bearer {token}"},
                timeout=60,
            )
        if response.status_code != 200:
            raise self._failure("Token verification", response)
        return response.json()

    def logout(self) -> None:
        self._tokens = None
        self.token_store.clear()

    def _parse_token_payload(self, payload: dict[str, Any]) -> TokenSet:
        access = payload.get("accessToken") or payload.get("access_token")
        refresh = payload.get("refreshToken") or payload.get("refresh_token")
        if not access or not refresh:
            raise AuthError("Token response missing required fields; response suppressed")
        access_exp = payload.get("accessTokenExpired") or payload.get("access_token_expires_at")
        if not access_exp and isinstance(payload.get("expires_in"), (int, float)):
            access_exp = (_utc_now() + timedelta(seconds=float(payload["expires_in"]))).isoformat()
        if not access_exp:
            access_exp = (_utc_now() + timedelta(minutes=55)).isoformat()
        refresh_exp = payload.get("refreshTokenExpired") or payload.get("refresh_token_expires_at")
        return TokenSet(
            access_token=str(access),
            refresh_token=str(refresh),
            access_token_expires_at=str(access_exp) if access_exp else None,
            refresh_token_expires_at=str(refresh_exp) if refresh_exp else None,
        )
