from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import responses

from simplifi.browser_login import is_token_response, parse_browser_tokens, verify_and_save
from simplifi.errors import AuthError, SimplifiHTTPError
from simplifi.models import TokenSet


@pytest.mark.parametrize(
    "url,method,status,expected",
    [
        ("https://services.quicken.com/oauth/token", "POST", 200, True),
        ("https://evil.example/oauth/token", "POST", 200, False),
        ("https://services.quicken.com.evil.example/oauth/token", "POST", 200, False),
        ("http://services.quicken.com/oauth/token", "POST", 200, False),
        ("https://services.quicken.com/oauth/authorize", "POST", 200, False),
        ("https://services.quicken.com/oauth/token", "GET", 200, False),
        ("https://services.quicken.com/oauth/token", "POST", 401, False),
        ("https://services.quicken.com/oauth/token?unexpected=1", "POST", 200, False),
    ],
)
def test_only_successful_quicken_token_response_matches(url, method, status, expected):
    response = SimpleNamespace(url=url, request=SimpleNamespace(method=method), status=status)
    assert is_token_response(response) is expected


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"accessToken": "secret"},
        {"accessToken": {"nested": "secret"}, "refreshToken": "secret"},
    ],
)
def test_reject_incomplete_or_malformed_tokens(payload):
    with pytest.raises(AuthError) as error:
        parse_browser_tokens(payload)
    assert "secret" not in str(error.value)


def test_token_parser_drops_unrelated_response_fields():
    tokens = parse_browser_tokens(
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
            "password": "never-store",
        }
    )
    assert tokens.access_token == "access"
    assert "never-store" not in tokens.model_dump_json()


@responses.activate
def test_verified_reads_precede_save(monkeypatch, capsys):
    store = Mock()
    receipt = Mock()
    monkeypatch.setattr("simplifi.local_cli.write_receipt", receipt)
    tokens = TokenSet(
        access_token="secret-access",
        refresh_token="secret-refresh",
        access_token_expires_at="2099-01-01T00:00:00Z",
    )
    responses.get("https://services.quicken.com/userprofiles/me", json={"id": "user"})
    responses.get("https://services.quicken.com/datasets", json={"resources": [{"id": "ds"}]})
    responses.get("https://services.quicken.com/accounts", json={"resources": [{"id": "acct"}]})
    responses.get("https://services.quicken.com/transactions", json={"resources": [{"id": "txn"}]})

    def save(verified):
        assert len(responses.calls) == 4
        assert verified.access_token == tokens.access_token

    store.save.side_effect = save
    result = verify_and_save(tokens, store=store)
    assert result["transaction_sample_count"] == 1
    store.save.assert_called_once()
    receipt.assert_called_once()
    assert "secret-access" not in capsys.readouterr().out


@responses.activate
def test_failed_reads_do_not_overwrite_cache_or_receipt(monkeypatch):
    store = Mock()
    receipt = Mock()
    monkeypatch.setattr("simplifi.local_cli.write_receipt", receipt)
    tokens = TokenSet(
        access_token="access",
        refresh_token="refresh",
        access_token_expires_at="2099-01-01T00:00:00Z",
    )
    responses.get("https://services.quicken.com/userprofiles/me", json={"id": "user"})
    responses.get("https://services.quicken.com/datasets", json={"resources": [{"id": "ds"}]})
    responses.get("https://services.quicken.com/accounts", status=403)
    with pytest.raises(SimplifiHTTPError):
        verify_and_save(tokens, store=store)
    store.save.assert_not_called()
    receipt.assert_not_called()


def test_browser_capture_closes_context_and_does_not_read_requests(monkeypatch):
    from simplifi.browser_login import capture_tokens

    for key in ("DEBUG", "PWDEBUG", "SSLKEYLOGFILE"):
        monkeypatch.delenv(key, raising=False)
    callback = None
    page = Mock()
    context = Mock()
    browser = Mock()
    browser.new_context.return_value = context
    context.new_page.return_value = page
    response = SimpleNamespace(
        url="https://services.quicken.com/oauth/token",
        status=200,
        request=SimpleNamespace(method="POST"),
        json=lambda: {"accessToken": "access", "refreshToken": "refresh"},
    )

    def on(event, handler):
        nonlocal callback
        assert event == "response"
        callback = handler

    context.on.side_effect = on
    page.goto.side_effect = lambda *args, **kwargs: callback(response)
    playwright = Mock()
    playwright.chromium.launch.return_value = browser
    manager = Mock()
    manager.__enter__ = Mock(return_value=playwright)
    manager.__exit__ = Mock(return_value=False)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: manager)
    tokens = capture_tokens()
    assert tokens.access_token == "access"
    playwright.chromium.launch.assert_called_once_with(channel="chrome", headless=False)
    browser.new_context.assert_called_once_with(accept_downloads=False)
    browser.close.assert_called_once()
    context.remove_listener.assert_called_once_with("response", callback)
