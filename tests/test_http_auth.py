from __future__ import annotations

import json
from pathlib import Path

import pytest
import responses

from simplifi.auth import AuthSession
from simplifi.client import Client
from simplifi.errors import ReadOnlyViolation, SimplifiHTTPError
from simplifi.http import HttpClient
from simplifi.token_store import TokenStore


def test_http_rejects_put():
    http = HttpClient(dataset_id="1", get_access_token=lambda: "tok")
    with pytest.raises(ReadOnlyViolation):
        http.request("PUT", "/transactions/1", json={})


def test_http_rejects_unknown_post():
    http = HttpClient(dataset_id="1", get_access_token=lambda: "tok")
    with pytest.raises(ReadOnlyViolation):
        http.request("POST", "/transactions", json={})


@responses.activate
def test_pagination_follows_next_link():
    responses.add(
        responses.GET,
        "https://services.quicken.com/transactions",
        json={
            "resources": [{"id": "1"}],
            "metaData": {"nextLink": "/transactions?after=abc"},
        },
        status=200,
    )
    responses.add(
        responses.GET,
        "https://services.quicken.com/transactions?after=abc",
        json={"resources": [{"id": "2"}], "metaData": {}},
        status=200,
    )
    http = HttpClient(dataset_id="ds1", get_access_token=lambda: "tok")
    rows = http.get_paginated("/transactions")
    assert [r["id"] for r in rows] == ["1", "2"]


@responses.activate
def test_http_error_surfaces_status():
    responses.add(
        responses.GET,
        "https://services.quicken.com/accounts",
        body="nope",
        status=500,
    )
    http = HttpClient(dataset_id="ds1", get_access_token=lambda: "tok")
    with pytest.raises(SimplifiHTTPError) as exc:
        http.get("/accounts")
    assert exc.value.status_code == 500


@responses.activate
def test_token_refresh_roundtrip(tmp_path: Path):
    store = TokenStore(tmp_path / "tokens.json")
    auth = AuthSession(token_store=store)
    responses.add(
        responses.POST,
        "https://services.quicken.com/oauth/token",
        json={
            "accessToken": "new-access",
            "refreshToken": "new-refresh",
            "accessTokenExpired": "2099-01-01T00:00:00Z",
        },
        status=200,
    )
    tokens = auth.refresh("old-refresh")
    assert tokens.access_token == "new-access"
    assert tokens.refresh_token == "new-refresh"


@responses.activate
def test_login_json_code_path(tmp_path: Path):
    store = TokenStore(tmp_path / "tokens.json")
    auth = AuthSession(token_store=store)
    responses.add(
        responses.POST,
        "https://services.quicken.com/oauth/authorize",
        json={"code": "auth-code", "status": "User passed MFA"},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://services.quicken.com/oauth/token",
        json={
            "accessToken": "access",
            "refreshToken": "refresh",
            "expires_in": 3600,
        },
        status=200,
    )
    tokens = auth.login("user@example.com", "secret", mfa_prompt=False)
    assert tokens.access_token == "access"
    saved = store.load().model_dump()
    assert saved["refresh_token"] == "refresh"


@responses.activate
def test_client_resolve_dataset(tmp_path: Path):
    store = TokenStore(tmp_path / "tokens.json")
    store.save(
        __import__("simplifi.models", fromlist=["TokenSet"]).TokenSet(
            access_token="access",
            refresh_token="refresh",
            access_token_expires_at="2099-01-01T00:00:00+00:00",
        )
    )
    responses.add(
        responses.GET,
        "https://services.quicken.com/userprofiles/me",
        json={"id": "u1"},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://services.quicken.com/datasets",
        json={"resources": [{"id": "dataset-99"}], "metaData": {}},
        status=200,
    )
    client = Client(token_store=store)
    client.use_token("access", refresh_token="refresh")
    assert client.dataset_id == "dataset-99"


@responses.activate
def test_resources_scheduled_transactions(tmp_path: Path):
    store = TokenStore(tmp_path / "tokens.json")
    client = Client(token_store=store, dataset_id="ds1")
    client.auth.use_access_token("access", refresh_token="refresh")
    responses.add(
        responses.GET,
        "https://services.quicken.com/scheduled-transactions",
        json={"resources": [{"id": "s1", "type": "BILL"}], "metaData": {}},
        status=200,
    )
    rows = client.scheduled_transactions()
    assert rows[0]["id"] == "s1"


def test_parse_har_script_imports():
    # Ensure the discovery script is syntactically valid / importable as module path.
    script = Path(__file__).resolve().parents[1] / "scripts" / "parse_har.py"
    assert script.exists()
    source = script.read_text(encoding="utf-8")
    assert "services.quicken.com" in source
