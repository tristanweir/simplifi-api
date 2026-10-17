import json

import pytest
import requests
import responses

from simplifi.auth import AuthSession
from simplifi.errors import AuthError
from simplifi.token_store import TokenStore


@responses.activate
def test_state_and_mfa_request_id_carried_through_login(tmp_path, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda _: "123456")
    states = []

    def authorize(request):
        state = request.headers["qcs-session-state"]
        assert request.headers["qcs-session-state-provided"] == "true"
        states.append(state)
        body = json.loads(request.body)
        if len(states) == 1:
            return (
                202,
                {"qcs-session-state": state},
                json.dumps(
                    {
                        "mfaChannel": "EMAIL",
                        "mfaId": "challenge",
                        "threatMetrixRequestId": "tm-request",
                    }
                ),
            )
        assert body["threatMetrixRequestId"] == "tm-request"
        assert request.headers["tm-request-id"] == "tm-request"
        return 200, {"qcs-session-state": state}, json.dumps({"code": "auth-code"})

    def token(request):
        assert request.headers["qcs-session-state"] == states[0]
        return 200, {}, json.dumps({"accessToken": "access", "refreshToken": "refresh"})

    responses.add_callback(
        "POST", "https://services.quicken.com/oauth/authorize", callback=authorize
    )
    responses.add_callback("POST", "https://services.quicken.com/oauth/token", callback=token)
    auth = AuthSession(token_store=TokenStore(tmp_path / "tokens.dpapi"))
    auth.login("email@example.com", "password")
    assert len(states) == 2 and states[0] == states[1]


def test_mismatched_state_prevents_token_exchange(tmp_path):
    auth = AuthSession(token_store=TokenStore(tmp_path / "tokens.dpapi"))
    auth._session_state = "ours"
    response = requests.Response()
    response.status_code = 200
    response.headers["qcs-session-state"] = "other"
    response._content = b'{"code":"auth-code"}'
    with pytest.raises(AuthError, match="session-state mismatch"):
        auth._extract_auth_code(response)


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"errors": [{"code": "QCS-0401-16", "detail": "password=do-not-print"}]}, "QCS-0401-16"),
        ({"code": "secret-access-token"}, None),
        ({"errors": [{"code": "QCS-0401-16\nsecret"}]}, None),
        (["secret"], None),
    ],
)
def test_only_safe_error_codes_are_exposed(payload, expected):
    response = requests.Response()
    response.status_code = 401
    response._content = json.dumps(payload).encode()
    error = str(AuthSession._failure("Authorize", response))
    assert "secret" not in error and "do-not-print" not in error
    if expected:
        assert expected in error
    else:
        assert "code=" not in error
