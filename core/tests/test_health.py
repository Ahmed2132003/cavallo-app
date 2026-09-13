"""
Tests for the /health/ endpoint (Part P-015).

Hits the real, permanently-wired endpoint (config/urls.py) directly via
Django's plain test Client -- unlike P-012's throwaway views, this is a
real production endpoint, so no @pytest.mark.urls override is needed.
"""

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return Client()


def test_health_check_ok_when_db_and_redis_up(client):
    response = client.get("/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "redis": "ok"}


def test_health_check_requires_no_authentication(client):
    """Load balancers/uptime checks hit this anonymously — must never
    require auth, unlike the project's DRF default (IsAuthenticated)."""
    response = client.get("/health/")

    assert response.status_code != 401
    assert response.status_code != 403


def test_health_check_503_when_db_down(client, monkeypatch):
    def _boom():
        raise Exception("simulated DB outage")

    monkeypatch.setattr(connection, "ensure_connection", _boom)

    response = client.get("/health/")
    body_text = response.content.decode()

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "db": "error", "redis": "ok"}
    # No exception detail or stack trace leaked to the client.
    assert "simulated DB outage" not in body_text
    assert "Traceback" not in body_text


def test_health_check_503_when_redis_down(client, monkeypatch):
    def _boom(*args, **kwargs):
        raise Exception("simulated Redis outage")

    monkeypatch.setattr(cache, "set", _boom)

    response = client.get("/health/")
    body_text = response.content.decode()

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "db": "ok", "redis": "error"}
    assert "simulated Redis outage" not in body_text
    assert "Traceback" not in body_text


def test_health_check_503_when_redis_get_does_not_match(client, monkeypatch):
    """A Redis that accepts writes but returns stale/wrong data on read
    should still fail the probe — not just a raised exception."""
    monkeypatch.setattr(cache, "get", lambda *args, **kwargs: "not-the-marker")

    response = client.get("/health/")

    assert response.status_code == 503
    assert response.json()["redis"] == "error"
