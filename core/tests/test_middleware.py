"""
Tests for RequestIdMiddleware (Part P-015).
"""

import uuid

import pytest
from django.test import Client

from core.logging_utils import request_id_ctx_var

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return Client()


def test_generates_request_id_when_none_provided(client):
    response = client.get("/health/")

    assert "X-Request-ID" in response
    # Raises ValueError if it isn't a real UUID4-parseable string.
    uuid.UUID(response["X-Request-ID"])


def test_honors_incoming_x_request_id_header(client):
    incoming = "caller-supplied-id-123"

    response = client.get("/health/", HTTP_X_REQUEST_ID=incoming)

    assert response["X-Request-ID"] == incoming


def test_two_requests_without_incoming_header_get_different_ids(client):
    first = client.get("/health/")
    second = client.get("/health/")

    assert first["X-Request-ID"] != second["X-Request-ID"]


def test_context_var_resets_after_request(client):
    assert request_id_ctx_var.get() == "no-request"

    client.get("/health/")

    # Scoped to the request — must not leak into whatever runs next.
    assert request_id_ctx_var.get() == "no-request"
