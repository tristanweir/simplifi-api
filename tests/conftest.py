"""Tests never fetch live OAuth metadata or authenticate to Quicken."""

import pytest


@pytest.fixture(autouse=True)
def synthetic_oauth_client(monkeypatch):
    monkeypatch.setattr("simplifi.auth.public_client_secret", lambda: "synthetic-web-client")
