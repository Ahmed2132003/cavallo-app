"""
Tests for Sentry initialization wiring (Part P-105, STEP 1).

No network and no database: sentry_sdk.init is always mocked, so these
tests can never send an event to a real Sentry project.
"""

import importlib
import sys
from unittest import mock

import pytest
import sentry_sdk

from config.sentry import init_sentry

FAKE_DSN = "https://publickey@o0.ingest.example.invalid/1"


def test_init_sentry_empty_dsn_is_disabled():
    with mock.patch("sentry_sdk.init") as fake_init:
        assert init_sentry("", "staging") is False
    fake_init.assert_not_called()


def test_init_sentry_with_dsn_initializes_with_environment():
    with mock.patch("sentry_sdk.init") as fake_init:
        assert init_sentry(FAKE_DSN, "staging") is True
    fake_init.assert_called_once_with(
        dsn=FAKE_DSN,
        environment="staging",
        traces_sample_rate=0.0,
        send_default_pii=False,
    )


def test_sentry_is_not_initialized_under_test_settings():
    # The test run uses config.settings.test -> dev -> base. None of them
    # may call sentry_sdk.init, whatever SENTRY_DSN holds in .env.
    assert not sentry_sdk.is_initialized()


@pytest.mark.parametrize(
    "module_name, expected_environment",
    [
        ("config.settings.staging", "staging"),
        ("config.settings.prod", "prod"),
    ],
)
def test_deployed_settings_call_init_sentry(
    monkeypatch, module_name, expected_environment
):
    monkeypatch.setenv("ALLOWED_HOSTS", "example.invalid")
    monkeypatch.setattr("config.settings.base.SENTRY_DSN", FAKE_DSN)
    fake = mock.Mock(return_value=True)
    monkeypatch.setattr("config.sentry.init_sentry", fake)
    sys.modules.pop(module_name, None)
    try:
        importlib.import_module(module_name)
    finally:
        sys.modules.pop(module_name, None)
    fake.assert_called_once_with(FAKE_DSN, environment=expected_environment)
