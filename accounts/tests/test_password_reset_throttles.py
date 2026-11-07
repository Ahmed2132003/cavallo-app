"""
Tests for Task P2.5 - throttling of the password reset endpoints.

Every endpoint has a per-IP layer and a per-email layer. Rates are lowered
through Django settings (read at request time), the clock is frozen where a
response is compared, and nothing sleeps. Counters live in the real Redis
cache; accounts/tests/conftest.py clears only the password reset throttle
keys around each test. Celery's .delay() is mocked so nothing is ever
published to the real broker. All data is fake.
"""

import hashlib
from unittest import mock
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from accounts import views
from accounts.throttles import LoginRateThrottle

User = get_user_model()

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "N3w-Str0ng-Uncommon-Pass!"

ENDPOINTS = {
    "request": (
        "accounts:password-reset-request",
        lambda email: {"email": email},
    ),
    "verify": (
        "accounts:password-reset-verify",
        lambda email: {"email": email, "otp": "123456"},
    ),
    "confirm": (
        "accounts:password-reset-confirm",
        lambda email: {
            "email": email,
            "reset_token": "A" * 43,
            "new_password": NEW_PASSWORD,
        },
    ),
}

HIGH = "1000/hour"


@pytest.fixture(autouse=True)
def _direct_client_ip(settings):
    # Use the socket address (REMOTE_ADDR) whatever NUM_PROXIES the local
    # .env sets, so the tests control the client IP deterministically.
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 0}


@pytest.fixture(autouse=True)
def _no_real_celery_publish():
    with mock.patch("accounts.views.send_password_reset_otp_task"):
        yield


@pytest.fixture
def frozen_clock(monkeypatch):
    # Same instant for every throttle decision, so Retry-After and the
    # "Expected available in N seconds" message are exactly comparable.
    monkeypatch.setattr(
        SimpleRateThrottle, "timer", staticmethod(lambda: 1_700_000_000.0)
    )


@pytest.fixture
def client():
    return APIClient()


def _fresh_email():
    return f"user-{uuid4().hex}@example.com"


def _set_rates(settings, *, ip=HIGH, identifier=HIGH):
    for name in ("REQUEST", "VERIFY", "CONFIRM"):
        setattr(settings, f"PASSWORD_RESET_{name}_IP_RATE", ip)
        setattr(settings, f"PASSWORD_RESET_{name}_IDENTIFIER_RATE", identifier)


def _post(client, endpoint, email, ip="10.0.0.1", **extra):
    url_name, payload = ENDPOINTS[endpoint]
    return client.post(
        reverse(url_name), payload(email), format="json", REMOTE_ADDR=ip, **extra
    )


def _stable_headers(response):
    # The request-id header is unique per request by design; ignore it.
    return {k.lower(): v for k, v in response.items() if "request" not in k.lower()}


# ------------------------------------------------------------ limits per layer


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_identifier_limit_allows_up_to_the_limit_then_429(client, settings, endpoint):
    _set_rates(settings, identifier="3/hour")
    email = _fresh_email()

    for _ in range(3):
        assert _post(client, endpoint, email).status_code != 429
    response = _post(client, endpoint, email)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "THROTTLED"
    assert int(response["Retry-After"]) > 0


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_ip_limit_allows_up_to_the_limit_then_429(client, settings, endpoint):
    _set_rates(settings, ip="3/hour")

    for _ in range(3):
        assert _post(client, endpoint, _fresh_email()).status_code != 429
    response = _post(client, endpoint, _fresh_email())

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "THROTTLED"
    assert int(response["Retry-After"]) > 0


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_identifier_limit_still_triggers_when_ip_changes(client, settings, endpoint):
    _set_rates(settings, identifier="3/hour")
    email = _fresh_email()

    for i in range(3):
        assert _post(client, endpoint, email, ip=f"10.1.0.{i}").status_code != 429
    response = _post(client, endpoint, email, ip="10.1.0.99")

    assert response.status_code == 429


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_ip_limit_still_triggers_when_identifier_changes(client, settings, endpoint):
    _set_rates(settings, ip="3/hour")

    for _ in range(3):
        assert _post(client, endpoint, _fresh_email(), ip="10.2.0.1").status_code != 429
    response = _post(client, endpoint, _fresh_email(), ip="10.2.0.1")

    assert response.status_code == 429


def test_email_is_normalized_before_counting(client, settings):
    _set_rates(settings, identifier="3/hour")
    local = uuid4().hex
    variants = [
        f"{local}@Example.com",
        f"  {local}@example.com ",
        f"{local}@EXAMPLE.COM",
    ]

    for variant in variants:
        assert _post(client, "request", variant).status_code != 429
    response = _post(client, "request", f"{local}@example.com")

    assert response.status_code == 429


# ------------------------------------------------------------ anti-enumeration


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_429_is_identical_for_existing_and_unknown_account(
    client, settings, frozen_clock, endpoint
):
    _set_rates(settings, identifier="2/hour")
    existing = _fresh_email()
    User.objects.create_user(
        username=existing, email=existing, password="0ld-Str0ng-Uncommon-Pass!"
    )
    unknown = _fresh_email()

    blocked = {}
    for label, email in (("existing", existing), ("unknown", unknown)):
        for _ in range(2):
            assert _post(client, endpoint, email).status_code != 429
        blocked[label] = _post(client, endpoint, email)

    a, b = blocked["existing"], blocked["unknown"]
    # The unknown account was counted and throttled exactly like the real one.
    assert a.status_code == b.status_code == 429
    assert a.json() == b.json()
    assert int(a["Retry-After"]) > 0
    assert _stable_headers(a) == _stable_headers(b)


# ------------------------------------------------------------------ separation


@pytest.mark.parametrize("layer", ["identifier", "ip"])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_scopes_do_not_share_counters(client, settings, endpoint, layer):
    if layer == "identifier":
        _set_rates(settings, identifier="2/hour")
    else:
        _set_rates(settings, ip="2/hour")
    shared_email = _fresh_email()

    def hit(name):
        email = shared_email if layer == "identifier" else _fresh_email()
        return _post(client, name, email)

    assert hit(endpoint).status_code != 429
    assert hit(endpoint).status_code != 429
    assert hit(endpoint).status_code == 429
    for other in ENDPOINTS:
        if other != endpoint:
            assert hit(other).status_code != 429


def test_throttles_are_attached_only_to_the_password_reset_views():
    for view in (
        views.PasswordResetRequestView,
        views.PasswordResetVerifyView,
        views.PasswordResetConfirmView,
    ):
        assert len(view.throttle_classes) == 2
        assert all(
            t.__name__.startswith("PasswordReset") for t in view.throttle_classes
        )

    assert views.LoginView.throttle_classes == [LoginRateThrottle]
    for view in (
        views.RegisterView,
        views.RefreshView,
        views.LogoutView,
        views.MeView,
    ):
        assert not any(
            t.__name__.startswith("PasswordReset") for t in view.throttle_classes
        )


# --------------------------------------------------------------- cache privacy


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_cache_keys_contain_the_hash_never_the_raw_email(client, endpoint):
    local = uuid4().hex
    email = f"Secret.{local}@Example.com"

    _post(client, endpoint, email)

    keys = cache.keys("throttle_password_reset_*")
    joined = "".join(keys)
    assert keys
    assert local not in joined.lower()
    assert "example.com" not in joined.lower()
    assert (
        hashlib.sha256(email.strip().casefold().encode("utf-8")).hexdigest() in joined
    )


# ------------------------------------------------------------------- client IP


def test_forged_x_forwarded_for_cannot_bypass_the_ip_limit(client, settings):
    # One trusted proxy: the client IP is the LAST X-Forwarded-For entry,
    # so rotating the forged leftmost entries must not reset the counter.
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
    _set_rates(settings, ip="3/hour")

    for i in range(3):
        response = _post(
            client,
            "request",
            _fresh_email(),
            HTTP_X_FORWARDED_FOR=f"198.51.100.{i}, 203.0.113.9",
        )
        assert response.status_code != 429
    response = _post(
        client,
        "request",
        _fresh_email(),
        HTTP_X_FORWARDED_FOR="198.51.100.77, 203.0.113.9",
    )

    assert response.status_code == 429


# ------------------------------------------------------------------ robustness


@pytest.mark.parametrize(
    "payload", [{}, {"email": None}, {"email": ["a@example.com"]}, []]
)
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_unusable_identifier_is_a_400_not_a_server_error(client, endpoint, payload):
    response = client.post(reverse(ENDPOINTS[endpoint][0]), payload, format="json")

    assert response.status_code == 400
