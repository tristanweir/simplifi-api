import json

import pytest
import responses

from simplifi.auth import AuthSession
from simplifi.client import Client
from simplifi.errors import AuthError, ReadOnlyViolation, SimplifiError
from simplifi.http import HttpClient
from simplifi.local_cli import check
from simplifi.models import TokenSet
from simplifi.secure_session import SecureSession
from simplifi.token_store import TokenStore


def test_dpapi_roundtrip_and_no_plaintext(tmp_path):
    store = TokenStore(tmp_path / "tokens.dpapi")
    tokens = TokenSet(
        access_token="secret-access",
        refresh_token="secret-refresh",
        web_client_secret="synthetic-web-metadata",
    )
    store.save(tokens)
    original = store.path.read_bytes()
    assert b"secret-access" not in original
    assert b"secret-refresh" not in original
    assert b"synthetic-web-metadata" not in original
    assert store.load() == tokens
    assert "secret-access" not in repr(tokens)
    assert "synthetic-web-metadata" not in repr(tokens)
    store.path.write_bytes(b"corrupt")
    with pytest.raises(AuthError):
        store.load()
    store.clear()
    assert store.load() is None


@responses.activate
def test_full_mfa_and_read_check(tmp_path, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda _: "123456")
    responses.post(
        "https://services.quicken.com/oauth/authorize",
        json={"mfaChannel": "EMAIL", "mfaId": "challenge"},
        status=202,
    )
    responses.post(
        "https://services.quicken.com/oauth/authorize",
        json={"code": "code", "status": "User passed MFA"},
    )
    responses.post(
        "https://services.quicken.com/oauth/token",
        json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600},
    )
    responses.get("https://services.quicken.com/userprofiles/me", json={"id": "user"})
    responses.get("https://services.quicken.com/datasets", json={"resources": [{"id": "dataset"}]})
    responses.get("https://services.quicken.com/accounts", json={"resources": [{"id": "account"}]})
    responses.get("https://services.quicken.com/transactions", json={"resources": [{"id": "txn"}]})
    client = Client(token_store=TokenStore(tmp_path / "tokens.dpapi"))
    client.auth.login("user@example.com", "password")
    result = check(client, record=False)
    assert result["account_count"] == result["transaction_sample_count"] == 1
    assert json.loads(responses.calls[1].request.body)["mfaId"] == "challenge"
    assert responses.calls[-1].request.headers["Qcs-Dataset-Id"] == "dataset"


@responses.activate
def test_expired_tokens_refresh_and_persist(tmp_path):
    store = TokenStore(tmp_path / "tokens.dpapi")
    store.save(
        TokenSet(
            access_token="old",
            refresh_token="refresh",
            access_token_expires_at="2000-01-01T00:00:00+00:00",
        )
    )
    responses.post(
        "https://services.quicken.com/oauth/token",
        json={"accessToken": "new", "refreshToken": "rotated", "expires_in": 3600},
    )
    auth = AuthSession(token_store=store)
    assert auth.ensure_access_token() == "new"
    assert store.load().refresh_token == "rotated"
    assert store.load().web_client_secret == "synthetic-web-client"


@pytest.mark.parametrize("error", [None, "invalid_client", "invalid_grant", "ambiguous"])
@responses.activate
def test_restart_refresh_uses_cached_metadata(tmp_path, monkeypatch, error):
    from unittest.mock import Mock

    discovery = Mock(return_value="new-web-metadata")
    monkeypatch.setattr("simplifi.auth.public_client_secret", discovery)
    store = TokenStore(tmp_path / "tokens.dpapi")
    original = TokenSet(
        access_token="old",
        refresh_token="refresh",
        web_client_secret="cached-web-metadata",
    )
    store.save(original)
    url = "https://services.quicken.com/oauth/token"
    if error:
        responses.post(url, status=401, json={"error": error, "detail": "do-not-print"})
    if error in (None, "invalid_client"):
        responses.post(url, json={"accessToken": "new", "refreshToken": "rotated"})

    auth = AuthSession(token_store=TokenStore(store.path))
    if error in ("invalid_grant", "ambiguous"):
        message = "rejected the refresh token" if error == "invalid_grant" else "HTTP 401"
        with pytest.raises(AuthError, match=message) as exc:
            auth.ensure_access_token()
        assert "do-not-print" not in str(exc.value)
        assert store.load() == original
    else:
        assert auth.ensure_access_token() == "new"
        assert store.load().refresh_token == "rotated"
        assert store.load().web_client_secret == (
            "new-web-metadata" if error == "invalid_client" else "cached-web-metadata"
        )

    secrets = ["cached-web-metadata"]
    if error == "invalid_client":
        secrets.append("new-web-metadata")
        discovery.assert_called_once_with()
    else:
        discovery.assert_not_called()
    assert [json.loads(call.request.body)["clientSecret"] for call in responses.calls] == secrets


@responses.activate
def test_discovery_failure_preserves_legacy_cache(tmp_path, monkeypatch):
    def unavailable():
        raise AuthError("Could not locate the public app bundle")

    monkeypatch.setattr("simplifi.auth.public_client_secret", unavailable)
    store = TokenStore(tmp_path / "tokens.dpapi")
    original = TokenSet(access_token="old", refresh_token="refresh")
    store.save(original)
    with pytest.raises(AuthError, match="OAuth configuration lookup failed"):
        AuthSession(token_store=store).ensure_access_token()
    assert not responses.calls
    assert store.load() == original


@pytest.mark.parametrize(
    "url",
    [
        "http://services.quicken.com/transactions",
        "https://evil.example/transactions",
        "https://services.quicken.com.evil.example/transactions",
    ],
)
def test_reject_external_origin(url):
    with pytest.raises(SimplifiError):
        SecureSession().get(url, headers={"Authorization": "Bearer secret"})


@responses.activate
def test_pagination_cannot_exfiltrate_token():
    responses.get(
        "https://services.quicken.com/transactions",
        json={"resources": [], "metaData": {"nextLink": "https://evil.example/steal"}},
    )
    http = HttpClient(dataset_id="ds", get_access_token=lambda: "secret")
    with pytest.raises(ReadOnlyViolation):
        http.get_paginated("/transactions")
    assert len(responses.calls) == 1


@responses.activate
def test_redirect_not_followed():
    responses.get(
        "https://services.quicken.com/accounts",
        status=302,
        headers={"Location": "https://evil.example/steal"},
    )
    with pytest.raises(SimplifiError):
        SecureSession().get("https://services.quicken.com/accounts")
    assert len(responses.calls) == 1


@responses.activate
def test_auth_error_does_not_reveal_response(tmp_path):
    responses.post(
        "https://services.quicken.com/oauth/token",
        status=400,
        json={"accessToken": "do-not-print", "password": "secret"},
    )
    auth = AuthSession(token_store=TokenStore(tmp_path / "tokens.dpapi"))
    with pytest.raises(AuthError) as error:
        auth.exchange_code("test")
    assert "do-not-print" not in str(error.value)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "POST"])
def test_all_transaction_writes_disabled(method):
    with pytest.raises(SimplifiError):
        SecureSession().request(method, "https://services.quicken.com/transactions/1")


@responses.activate
def test_read_401_refreshes_once(tmp_path):
    store = TokenStore(tmp_path / "tokens.dpapi")
    store.save(
        TokenSet(
            access_token="expired-on-server",
            refresh_token="refresh",
            access_token_expires_at="2099-01-01T00:00:00+00:00",
        )
    )
    responses.get("https://services.quicken.com/transactions", status=401)
    responses.post(
        "https://services.quicken.com/oauth/token",
        json={"accessToken": "new", "refreshToken": "rotated", "expires_in": 3600},
    )
    responses.get("https://services.quicken.com/transactions", json={"resources": [{"id": "txn"}]})
    client = Client(token_store=store, dataset_id="ds")
    assert client.transactions()[0]["id"] == "txn"
    assert responses.calls[-1].request.headers["Authorization"] == "Bearer new"
    assert store.load().refresh_token == "rotated"
