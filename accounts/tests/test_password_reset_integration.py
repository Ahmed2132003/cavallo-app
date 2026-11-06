"""
Tests for Task P2.6 - password reset integration over the real HTTP
endpoints.

Complements test_password_reset_api.py (P2.4) and
test_password_reset_throttles.py (P2.5) without repeating them. The real
Celery task runs eagerly and the OTP is read from the email in mail.outbox.
Rates are only lowered where a test needs to trip a limit; the test that
proves a legitimate user is never throttled uses the default rates.
The clock is frozen where two 429 responses are compared, and nothing
sleeps. Redis keys are removed per address (no cache.clear()); the reset
throttle counters are cleared by accounts/tests/conftest.py and the login
throttle counters by the fixture below. All data is fake.
"""

import logging
import re
from unittest import mock
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from django_redis import get_redis_connection
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from accounts import services
from config.celery import app as celery_app

User = get_user_model()

pytestmark = pytest.mark.django_db

OLD_PASSWORD = "0ld-Str0ng-Uncommon-Pass!"
NEW_PASSWORD = "N3w-Str0ng-Uncommon-Pass!"
THIRD_PASSWORD = "An0ther-Str0ng-Pass!"

URL_REQUEST = "accounts:password-reset-request"
URL_VERIFY = "accounts:password-reset-verify"
URL_CONFIRM = "accounts:password-reset-confirm"
URL_LOGIN = "accounts:login"
URL_REFRESH = "accounts:refresh"

URL_BY_ENDPOINT = {
    "request": URL_REQUEST,
    "verify": URL_VERIFY,
    "confirm": URL_CONFIRM,
}

HIGH = "1000/hour"
FAILED_LOGIN = (401, 403)
_LOGIN_THROTTLE_PATTERN = "throttle_login_*"


# --------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def eager_celery():
    old_eager = celery_app.conf.task_always_eager
    old_propagate = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    yield
    celery_app.conf.task_always_eager = old_eager
    celery_app.conf.task_eager_propagates = old_propagate


@pytest.fixture(autouse=True)
def _clean_login_throttle_cache():
    # The login throttle shares the cache with the reset throttles but is not
    # cleared by conftest.py. Delete only its keys (never cache.clear()).
    cache.delete_pattern(_LOGIN_THROTTLE_PATTERN)
    yield
    cache.delete_pattern(_LOGIN_THROTTLE_PATTERN)


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(
        SimpleRateThrottle, "timer", staticmethod(lambda: 1_700_000_000.0)
    )


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def redis_conn():
    return get_redis_connection("default")


@pytest.fixture
def cleanup(redis_conn):
    """Collects emails whose Redis keys are deleted after the test."""
    emails = []
    yield emails
    for address in emails:
        normalized = services._normalize_email(address)
        redis_conn.delete(
            *services._otp_keys(normalized),
            services._reset_token_key(normalized),
        )


def _make_user(cleanup, *, prefix="Flow"):
    address = f"{prefix}.Case-{uuid4().hex}@Example.com"
    cleanup.append(address)
    return User.objects.create_user(
        username=address, email=address, password=OLD_PASSWORD
    )


@pytest.fixture
def user(cleanup):
    return _make_user(cleanup)


# ---------------------------------------------------------------- helpers


def _post(client, name, payload):
    return client.post(reverse(name), payload, format="json")


def _snapshot(response):
    return response.status_code, response.json()


def _set_rates(settings, *, ip=HIGH, identifier=HIGH):
    for name in ("REQUEST", "VERIFY", "CONFIRM"):
        setattr(settings, f"PASSWORD_RESET_{name}_IP_RATE", ip)
        setattr(settings, f"PASSWORD_RESET_{name}_IDENTIFIER_RATE", identifier)


def _otp_from_outbox(index=-1):
    return re.search(r"\b(\d{6})\b", mail.outbox[index].body).group(1)


def _request_otp(client, email):
    before = len(mail.outbox)
    response = _post(client, URL_REQUEST, {"email": email})
    assert response.status_code == 202
    assert len(mail.outbox) == before + 1
    return _otp_from_outbox()


def _wrong_otp(otp):
    return "000000" if otp != "000000" else "111111"


def _verify(client, email, otp):
    return _post(client, URL_VERIFY, {"email": email, "otp": otp})


def _confirm(client, email, token, password=NEW_PASSWORD):
    return _post(
        client,
        URL_CONFIRM,
        {"email": email, "reset_token": token, "new_password": password},
    )


def _login(client, email, password):
    return _post(client, URL_LOGIN, {"email": email, "password": password})


def _reset_through_http(client, email, password=NEW_PASSWORD):
    """request -> verify -> confirm, all through the real endpoints."""
    otp = _request_otp(client, email)
    verified = _verify(client, email, otp)
    assert verified.status_code == 200
    token = verified.json()["reset_token"]
    assert _confirm(client, email, token, password).status_code == 200
    return otp, token


def _reset_payload(endpoint, email):
    return {
        "request": {"email": email},
        "verify": {"email": email, "otp": "123456"},
        "confirm": {
            "email": email,
            "reset_token": "A" * 43,
            "new_password": NEW_PASSWORD,
        },
    }[endpoint]


# ------------------------------------------------------------- happy path


def test_full_flow_over_http_then_login_works_only_with_the_new_password(
    client, user
):
    assert _login(client, user.email, OLD_PASSWORD).status_code == 200

    otp = _request_otp(client, user.email)
    verified = _verify(client, user.email, otp)
    assert verified.status_code == 200
    confirmed = _confirm(client, user.email, verified.json()["reset_token"])
    assert confirmed.status_code == 200
    assert confirmed.json() == {"detail": "Password has been reset."}

    login = _login(client, user.email, NEW_PASSWORD)
    assert login.status_code == 200
    assert set(login.json()) >= {"access", "refresh"}
    old = _login(client, user.email, OLD_PASSWORD)
    assert old.status_code in FAILED_LOGIN
    assert "access" not in old.json()


def test_legitimate_flow_is_never_throttled_at_default_rates(client, user):
    # Deliberately no _set_rates(): the configured defaults are used.
    request = _post(client, URL_REQUEST, {"email": user.email})
    otp = _otp_from_outbox()
    verified = _verify(client, user.email, otp)
    token = verified.json().get("reset_token", "")
    confirmed = _confirm(client, user.email, token)

    assert [r.status_code for r in (request, verified, confirmed)] == [202, 200, 200]


# ----------------------------------------------------------- after confirm


def test_otp_and_reset_token_cannot_be_reused_after_confirm(
    client, settings, redis_conn, user
):
    _set_rates(settings)
    otp, token = _reset_through_http(client, user.email)

    replay = _verify(client, user.email, otp)
    assert replay.status_code == 400
    assert "reset_token" not in str(replay.json())
    # The replayed OTP minted nothing.
    normalized = services._normalize_email(user.email)
    assert redis_conn.exists(services._reset_token_key(normalized)) == 0

    again = _confirm(client, user.email, token, password=THIRD_PASSWORD)
    assert again.status_code == 400
    assert _login(client, user.email, NEW_PASSWORD).status_code == 200
    assert _login(client, user.email, THIRD_PASSWORD).status_code in FAILED_LOGIN


def test_confirm_blacklists_refresh_tokens_from_real_logins_and_spares_others(
    client, cleanup, user
):
    other = _make_user(cleanup, prefix="Other")
    other_pair = services.issue_token_pair(other)
    first = _login(client, user.email, OLD_PASSWORD).json()
    second = _login(client, user.email, OLD_PASSWORD).json()
    assert OutstandingToken.objects.filter(user=user).count() == 2

    _reset_through_http(client, user.email)

    assert BlacklistedToken.objects.filter(token__user=user).count() == 2
    for pair in (first, second):
        assert _post(client, URL_REFRESH, {"refresh": pair["refresh"]}).status_code == 401
    # A session opened with the new password works.
    fresh = _login(client, user.email, NEW_PASSWORD).json()
    assert _post(client, URL_REFRESH, {"refresh": fresh["refresh"]}).status_code == 200
    # The other account is untouched.
    assert not BlacklistedToken.objects.filter(token__user=other).exists()
    assert (
        _post(client, URL_REFRESH, {"refresh": other_pair["refresh"]}).status_code
        == 200
    )
    assert services.authenticate_user(email=other.email, password=OLD_PASSWORD)


# ------------------------------------------------- lockout and throttling


def test_otp_lockout_rejects_the_correct_otp_and_a_new_request_clears_it(
    client, settings, redis_conn, user
):
    _set_rates(settings)
    otp = _request_otp(client, user.email)
    wrong = _wrong_otp(otp)

    failures = [
        _snapshot(_verify(client, user.email, wrong))
        for _ in range(services.OTP_MAX_ATTEMPTS)
    ]
    locked = _snapshot(_verify(client, user.email, otp))

    assert failures[0][0] == 400
    assert all(f == failures[0] for f in failures)
    assert locked == failures[0]
    normalized = services._normalize_email(user.email)
    assert redis_conn.exists(services._reset_token_key(normalized)) == 0

    # A fresh request issues a new OTP with a reset attempt counter.
    new_otp = _request_otp(client, user.email)
    assert _verify(client, user.email, new_otp).status_code == 200


def test_verify_throttle_returns_429_before_the_otp_is_checked(
    client, settings, frozen_clock, redis_conn, user
):
    _set_rates(settings)
    settings.PASSWORD_RESET_VERIFY_IDENTIFIER_RATE = "3/hour"
    otp = _request_otp(client, user.email)
    wrong = _wrong_otp(otp)

    for _ in range(3):
        assert _verify(client, user.email, wrong).status_code == 400

    blocked_correct = _verify(client, user.email, otp)
    blocked_wrong = _verify(client, user.email, wrong)

    assert blocked_correct.status_code == blocked_wrong.status_code == 429
    assert blocked_correct.json()["error"]["code"] == "THROTTLED"
    assert blocked_correct.json() == blocked_wrong.json()
    assert blocked_correct["Retry-After"] == blocked_wrong["Retry-After"]
    assert "reset_token" not in str(blocked_correct.json())
    # The throttle answered before the service ran: no token, OTP untouched.
    normalized = services._normalize_email(user.email)
    otp_key, _attempts_key = services._otp_keys(normalized)
    assert redis_conn.exists(services._reset_token_key(normalized)) == 0
    assert redis_conn.exists(otp_key) == 1
    # Straight to the service (bypassing the throttle): 3 of 5 attempts used.
    assert services.verify_password_reset_otp(user.email, otp) is True


def test_login_is_unaffected_by_exhausted_reset_throttles(client, settings, user):
    _set_rates(settings, ip="1/hour", identifier="1/hour")

    for endpoint in URL_BY_ENDPOINT:
        payload = _reset_payload(endpoint, user.email)
        first = _post(client, URL_BY_ENDPOINT[endpoint], payload)
        second = _post(client, URL_BY_ENDPOINT[endpoint], payload)
        assert first.status_code != 429
        assert second.status_code == 429

    assert _login(client, user.email, OLD_PASSWORD).status_code == 200
    wrong = _login(client, user.email, "Wr0ng-Str0ng-Uncommon-Pass!")
    assert wrong.status_code in FAILED_LOGIN
    # The reset scopes stayed exhausted the whole time.
    assert (
        _post(client, URL_REQUEST, {"email": user.email}).status_code == 429
    )


# ------------------------------------------------------------ OTP reissue


def test_requesting_a_new_otp_invalidates_the_previous_one(client, settings, user):
    _set_rates(settings)
    # Fixed OTPs so the two issuances can never collide by chance.
    with mock.patch.object(services.secrets, "randbelow", side_effect=[111111, 222222]):
        first = _request_otp(client, user.email)
        second = _request_otp(client, user.email)

    assert (first, second) == ("111111", "222222")
    assert len(mail.outbox) == 2

    old = _verify(client, user.email, first)
    assert old.status_code == 400
    assert "reset_token" not in str(old.json())
    assert _verify(client, user.email, second).status_code == 200


# ------------------------------------------------------------ secret hygiene


def test_flow_never_leaks_otp_token_or_passwords_into_logs_or_responses(
    client, user, caplog
):
    caplog.set_level(logging.DEBUG)

    with mock.patch.object(services.secrets, "randbelow", return_value=482915):
        request = _post(client, URL_REQUEST, {"email": user.email})
    otp = _otp_from_outbox()
    assert otp == "482915"
    verified = _verify(client, user.email, otp)
    token = verified.json()["reset_token"]
    weak = _confirm(client, user.email, token, password="12345678")
    confirmed = _confirm(client, user.email, token)
    assert (request.status_code, verified.status_code) == (202, 200)
    assert weak.status_code == 400
    assert confirmed.status_code == 200

    # The token is returned in the verify body by design, so that body is
    # only checked for the passwords and nothing else.
    for body in (request.content.decode(), weak.content.decode(), confirmed.content.decode()):
        for secret in (otp, token, NEW_PASSWORD, OLD_PASSWORD):
            assert secret not in body
    assert NEW_PASSWORD not in verified.content.decode()
    assert OLD_PASSWORD not in verified.content.decode()
    for secret in (otp, token, NEW_PASSWORD, OLD_PASSWORD):
        assert secret not in caplog.text