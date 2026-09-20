"""
Tests for ConflictError and the CONFLICT envelope code (Part P-038).

Calls the handler directly with a ConflictError instead of going
through a throwaway view, since no route is needed to prove the mapping.
"""

import pytest

from core.exceptions import ConflictError, custom_exception_handler

pytestmark = pytest.mark.django_db


def test_conflict_error_is_http_409_with_conflict_code():
    assert ConflictError.status_code == 409
    assert ConflictError.default_code == "conflict"


def test_handler_returns_409_with_conflict_envelope():
    response = custom_exception_handler(ConflictError(), {})

    assert response.status_code == 409
    assert set(response.data.keys()) == {"error"}
    error = response.data["error"]
    assert error["code"] == "CONFLICT"
    assert error["message"] == (
        "The request conflicts with the current state of the resource."
    )
    assert error["fields"] == {}


def test_handler_uses_the_custom_message():
    response = custom_exception_handler(ConflictError("Already decided."), {})

    assert response.status_code == 409
    assert response.data["error"]["code"] == "CONFLICT"
    assert response.data["error"]["message"] == "Already decided."
