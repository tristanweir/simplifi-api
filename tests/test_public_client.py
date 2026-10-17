import pytest
import responses

from simplifi.errors import AuthError
from simplifi.public_client import public_client_secret


@responses.activate
def test_public_metadata_is_resolved_without_user_credentials():
    responses.get("https://simplifi.quicken.com/", body='<script src="/main.example.js"></script>')
    responses.get(
        "https://simplifi.quicken.com/main.example.js",
        body='{"client_id":"acme_web","client_secret":"synthetic-value"}',
    )
    assert public_client_secret() == "synthetic-value"
    assert all("Authorization" not in call.request.headers for call in responses.calls)


@responses.activate
def test_public_metadata_lookup_rejects_redirects():
    responses.get(
        "https://simplifi.quicken.com/", status=302, headers={"Location": "https://evil.example"}
    )
    with pytest.raises(AuthError):
        public_client_secret()
    assert len(responses.calls) == 1
