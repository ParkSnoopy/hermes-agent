"""Endpoint identity and credentials resolve from the same multiplexed profile."""

import os

import pytest

from agent import secret_scope as ss
from gateway.platforms._shared import extra_or_secret, get_scoped_secret


DEFAULT_ENV = {"FIXTURE_ENDPOINT": "https://default.example", "FIXTURE_TOKEN": "default-token"}
SECONDARY = {"FIXTURE_ENDPOINT": "https://secondary.example", "FIXTURE_TOKEN": "secondary-token"}


@pytest.fixture
def secondary_scope(monkeypatch):
    for key, value in DEFAULT_ENV.items():
        monkeypatch.setenv(key, value)
    ss.set_multiplex_active(True)
    token = ss.set_secret_scope(SECONDARY)
    yield
    ss.reset_secret_scope(token)
    ss.set_multiplex_active(False)


def test_endpoint_identity_and_token_follow_same_profile(secondary_scope):
    endpoint = extra_or_secret({}, "endpoint", "FIXTURE_ENDPOINT")
    token = get_scoped_secret("FIXTURE_TOKEN")
    assert (endpoint, token) == (SECONDARY["FIXTURE_ENDPOINT"], SECONDARY["FIXTURE_TOKEN"])
    assert os.environ["FIXTURE_ENDPOINT"] == DEFAULT_ENV["FIXTURE_ENDPOINT"]


def test_missing_scoped_identity_uses_profile_config_not_default_env(monkeypatch):
    monkeypatch.setenv("FIXTURE_ENDPOINT", DEFAULT_ENV["FIXTURE_ENDPOINT"])
    ss.set_multiplex_active(True)
    token = ss.set_secret_scope({"FIXTURE_TOKEN": SECONDARY["FIXTURE_TOKEN"]})
    try:
        assert extra_or_secret({"endpoint": "https://yaml.example"}, "endpoint", "FIXTURE_ENDPOINT") == "https://yaml.example"
        assert get_scoped_secret("FIXTURE_TOKEN") == SECONDARY["FIXTURE_TOKEN"]
    finally:
        ss.reset_secret_scope(token)
        ss.set_multiplex_active(False)
