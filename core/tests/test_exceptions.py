"""
Tests for `custom_exception_handler` (Part P-012).

Hits real throwaway views (core/tests/views.py) through the full DRF
view-dispatch path via `core/tests/urls.py`, which is only ever wired
in here via `override_settings(ROOT_URLCONF=...)` — it's never part of
the real `config/urls.py`.
"""

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

# pytest.mark.urls is pytest-django's own mechanism for pointing a test
# module at a different ROOT_URLCONF for the duration of its tests —
# the documented way to do exactly what core/tests/urls.py's docstring
# describes.
pytestmark = [pytest.mark.django_db, pytest.mark.urls("core.tests.urls")]


@pytest.fixture
def client():
    return APIClient()


def test_happy_path_untouched(client):
    """The handler must be a total no-op on a successful response."""
    response = client.get("/ok/")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_validation_error_envelope(client):
    response = client.get("/validation-error/")

    assert response.status_code == 400
    body = response.json()
    assert set(body.keys()) == {"error"}
    error = body["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert isinstance(error["message"], str) and error["message"]
    assert error["fields"] == {"email": ["This field is required."]}


def test_authentication_failed_envelope(client):
    response = client.get("/auth-failed/")

    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "AUTHENTICATION_FAILED"
    assert error["message"] == "Invalid credentials."
    assert error["fields"] == {}


def test_permission_denied_envelope(client):
    response = client.get("/permission-denied/")

    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "PERMISSION_DENIED"
    assert error["message"] == "You do not have permission to do that."
    assert error["fields"] == {}


def test_not_found_envelope(client):
    response = client.get("/not-found/")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["message"] == "No such widget."
    assert error["fields"] == {}


def test_django_404_still_shaped_by_default_drf_behavior(client):
    """
    A URL that doesn't exist at all is a plain Django 404 (never even
    reaches a DRF view), so it does NOT go through our handler and does
    NOT come back in the envelope shape. This test documents that
    boundary explicitly rather than leaving it as a surprise.
    """
    response = client.get("/this-path-does-not-exist/")
    assert response.status_code == 404
    # Plain Django 404 HTML page, not our JSON envelope.
    assert response["Content-Type"].startswith("text/html")


@override_settings(DEBUG=False)
def test_unhandled_exception_returns_generic_server_error_when_debug_false(client):
    response = client.get("/unhandled/")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "SERVER_ERROR"
    assert error["message"] == "An unexpected error occurred."
    assert error["fields"] == {}


@override_settings(DEBUG=True)
def test_unhandled_exception_propagates_in_debug_true(client):
    """
    In DEBUG=True, `custom_exception_handler` must NOT intercept an
    unhandled exception into the JSON envelope — it returns None and
    lets DRF's own `raise_uncaught_exception` re-raise the original
    exception, exactly as DRF's default handler would with no
    EXCEPTION_HANDLER override at all. Django's test client (which
    APIClient is built on) surfaces that re-raised exception directly
    to the test by design (`raise_request_exception=True`, the
    default) rather than converting it to a response — which is
    exactly what "debugging isn't harder" means in practice: a real
    unhandled bug still fails loudly instead of being silently wrapped.

    Note: pytest-django forces DEBUG=False for every test by default
    (regardless of the settings module), which is exactly why the
    DEBUG=False case above needed no override but this one does.
    """
    with pytest.raises(RuntimeError, match="Deliberately unhandled"):
        client.get("/unhandled/")
