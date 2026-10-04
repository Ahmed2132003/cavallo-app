"""
Tests for the Sentry capture in custom_exception_handler (Part P-105, STEP 2).

sentry_sdk.capture_exception is always mocked: no network, and nothing can
reach a real Sentry project from the test run.
"""

import inspect
from unittest import mock

import pytest
import sentry_sdk
from django.test import override_settings
from rest_framework.test import APIClient
from sentry_sdk.transport import Transport

from core.exceptions import custom_exception_handler

pytestmark = [pytest.mark.django_db, pytest.mark.urls("core.tests.urls")]


class _ListTransport(Transport):
    """In-memory transport: collects events instead of sending them."""

    def __init__(self, sink):
        super().__init__()
        self.sink = sink

    def capture_envelope(self, envelope):
        event = envelope.get_event()
        if event is not None:
            self.sink.append(event)


@pytest.fixture
def client():
    return APIClient()


def test_handler_signature_is_unchanged():
    # P-012's contract: only the BODY may change in P-105, never the signature.
    params = list(inspect.signature(custom_exception_handler).parameters)
    assert params == ["exc", "context"]


@override_settings(DEBUG=False)
def test_unhandled_exception_is_captured_once_and_response_is_unchanged(client):
    with mock.patch("sentry_sdk.capture_exception") as fake_capture:
        response = client.get("/unhandled/")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "SERVER_ERROR",
            "message": "An unexpected error occurred.",
            "fields": {},
        }
    }
    fake_capture.assert_called_once()
    captured = fake_capture.call_args.args[0]
    assert isinstance(captured, RuntimeError)
    assert "Deliberately unhandled" in str(captured)


@override_settings(DEBUG=False)
@pytest.mark.parametrize(
    "path",
    [
        "/ok/",
        "/validation-error/",
        "/auth-failed/",
        "/permission-denied/",
        "/not-found/",
    ],
)
def test_handled_errors_are_not_captured(client, path):
    with mock.patch("sentry_sdk.capture_exception") as fake_capture:
        client.get(path)

    fake_capture.assert_not_called()


@override_settings(DEBUG=True)
def test_debug_true_path_does_not_capture_here(client):
    # DEBUG=True still propagates the original exception (P-012); the
    # explicit capture only belongs to the DEBUG=False branch.
    with mock.patch("sentry_sdk.capture_exception") as fake_capture:
        with pytest.raises(RuntimeError):
            client.get("/unhandled/")

    fake_capture.assert_not_called()


@override_settings(DEBUG=False)
def test_unhandled_exception_reaches_a_real_sentry_client(client):
    # Not mocked at the capture call: a real sentry_sdk client with an
    # in-memory transport proves the event is actually produced. The client
    # is removed afterwards so no other test sees an initialized SDK.
    events = []
    sentry_sdk.init(
        dsn="https://publickey@o0.ingest.example.invalid/1",
        transport=_ListTransport(events),
        traces_sample_rate=0.0,
    )
    try:
        response = client.get("/unhandled/")
        sentry_sdk.flush()
    finally:
        sentry_sdk.get_global_scope().set_client(None)

    assert response.status_code == 500
    assert not sentry_sdk.is_initialized()
    assert len(events) == 1
    exception = events[0]["exception"]["values"][-1]
    assert exception["type"] == "RuntimeError"
    assert "Deliberately unhandled" in exception["value"]
