"""
Tests for Task P2.4 - password reset API (request, verify, confirm).

Like the earlier password reset tests, these use the real Redis configured
for the test settings. Every test uses unique email addresses and the
fixtures delete only the keys derived from those addresses; the database is
never flushed. Email goes through Django's locmem backend (mail.outbox).
Celery runs eagerly inside these tests. All data is fake.
"""

import logging
import re
import smtplib
from unittest import mock
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse
from django_redis import get_redis_connection
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from accounts import services
from accounts.throttles import LoginRateThrottle
from config.celery import app as celery_app

User = get_user_model()

pytestmark = pytest.mark.django_db

OLD_PASSWORD = "0ld-Str0ng-Uncommon-Pass!"
NEW_PASSWORD = "N3w-Str0ng-Uncommon-Pass!"

URL_REQUEST = "accounts:password-reset-request"
URL_VERIFY = "accounts:password-reset-verify"
URL_CONFIRM = "accounts:password-reset-confirm"


@pytest.fixture(autouse=True)
def eager_celery():
    old_eager = celery_app.conf.task_always_eager
    old_propagate = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    yield
    celery_app.conf.task_always_eager = old_eager
    celery_app.conf.task_eager_propagates = old_propagate


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


def _make_user(cleanup, *, active=True, password=OLD_PASSWORD, prefix="User"):
    address = f"{prefix}.Case-{uuid4().hex}@Example.com"
    cleanup.append(address)
    return User.objects.create_user(
        username=address, email=address, password=password, is_active=active
    )


@pytest.fixture
def user(cleanup):
    return _make_user(cleanup)


@pytest.fixture
def unknown_email(cleanup):
    address = f"nobody-{uuid4().hex}@example.com"
    cleanup.append(address)
    return address


def _post(client, name, payload):
    return client.post(reverse(name), payload, format="json")


def _snapshot(response):
    return response.status_code, response.json()


def _redis_keys_for(redis_conn, email):
    email_id = services._otp_keys(services._normalize_email(email))[0].rsplit(":", 1)[1]
    return sorted(k.decode() for k in redis_conn.scan_iter(match=f"*{email_id}*"))


def _issue_reset_token(email):
    otp = services.create_password_reset_otp(email)
    token = services.verify_otp_and_issue_reset_token(email, otp)
    assert token is not None
    return token


def _confirm(client, email, token, password=NEW_PASSWORD):
    return _post(
        client,
        URL_CONFIRM,
        {"email": email, "reset_token": token, "new_password": password},
    )


# ---------------------------------------------------------------- request


def test_request_existing_active_user_sends_one_email_with_valid_otp(client, user):
    response = _post(client, URL_REQUEST, {"email": user.email})

    assert response.status_code == 202
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    otp = re.search(r"\b(\d{6})\b", mail.outbox[0].body).group(1)
    assert services.verify_password_reset_otp(user.email, otp) is True


def test_request_unknown_and_inactive_users_send_nothing(
    client, redis_conn, unknown_email, cleanup
):
    inactive = _make_user(cleanup, active=False)

    for address in (unknown_email, inactive.email):
        response = _post(client, URL_REQUEST, {"email": address})
        assert response.status_code == 202

    assert mail.outbox == []
    assert _redis_keys_for(redis_conn, unknown_email) == []
    assert _redis_keys_for(redis_conn, inactive.email) == []


def test_request_response_is_identical_for_registered_and_unknown(
    client, user, unknown_email
):
    known = _post(client, URL_REQUEST, {"email": user.email})
    unknown = _post(client, URL_REQUEST, {"email": unknown_email})

    assert known.status_code == unknown.status_code
    assert known.json() == unknown.json()


def test_request_invalid_email_format_is_a_validation_error(client):
    response = _post(client, URL_REQUEST, {"email": "not-an-email"})

    assert response.status_code == 400


def test_request_view_only_queues_the_task_with_the_email(
    client, user, unknown_email, django_assert_num_queries
):
    with mock.patch("accounts.views.send_password_reset_otp_task") as task, mock.patch(
        "accounts.services.request_password_reset_otp"
    ) as service, mock.patch("accounts.services.send_mail") as smtp, mock.patch(
        "accounts.services.get_redis_connection"
    ) as redis_factory:
        # Zero database queries: the view itself does no lookup.
        with django_assert_num_queries(0):
            _post(client, URL_REQUEST, {"email": user.email})
            _post(client, URL_REQUEST, {"email": unknown_email})

    assert task.delay.call_args_list == [
        mock.call(user.email),
        mock.call(unknown_email),
    ]
    service.assert_not_called()
    smtp.assert_not_called()
    redis_factory.assert_not_called()


def test_request_smtp_failure_inside_task_is_swallowed_and_logged(
    client, user, unknown_email, caplog
):
    captured = []

    def failing_send(target, otp):
        captured.append(otp)
        raise smtplib.SMTPException(f"refused for {target.email}")

    expected = _snapshot(_post(client, URL_REQUEST, {"email": unknown_email}))
    with caplog.at_level(logging.ERROR), mock.patch.object(
        services, "_send_password_reset_otp_email", side_effect=failing_send
    ):
        response = _post(client, URL_REQUEST, {"email": user.email})

    assert _snapshot(response) == expected
    assert mail.outbox == []
    assert captured and captured[0] not in caplog.text
    assert user.email not in caplog.text
    assert "SMTPException" in caplog.text


def test_request_redis_failure_inside_task_is_swallowed_and_logged(
    client, user, unknown_email, caplog
):
    expected = _snapshot(_post(client, URL_REQUEST, {"email": unknown_email}))
    with caplog.at_level(logging.ERROR), mock.patch.object(
        services,
        "create_password_reset_otp",
        side_effect=RedisConnectionError(f"down for {user.email}"),
    ):
        response = _post(client, URL_REQUEST, {"email": user.email})

    assert _snapshot(response) == expected
    assert mail.outbox == []
    assert user.email not in caplog.text
    assert "ConnectionError" in caplog.text


def test_request_enqueue_failure_returns_the_same_response(
    client, user, unknown_email, caplog
):
    expected = _snapshot(_post(client, URL_REQUEST, {"email": unknown_email}))
    with caplog.at_level(logging.ERROR), mock.patch(
        "accounts.views.send_password_reset_otp_task"
    ) as task:
        task.delay.side_effect = RuntimeError(f"broker down for {user.email}")
        response = _post(client, URL_REQUEST, {"email": user.email})

    assert _snapshot(response) == expected
    assert user.email not in caplog.text
    assert "RuntimeError" in caplog.text


# ----------------------------------------------------------------- verify


def test_verify_success_returns_a_reset_token(client, user):
    otp = services.create_password_reset_otp(user.email)

    response = _post(client, URL_VERIFY, {"email": user.email, "otp": otp})

    assert response.status_code == 200
    token = response.json()["reset_token"]
    assert services.consume_password_reset_token(user.email, token) is True


def test_verify_every_failure_kind_gives_the_same_response(
    client, redis_conn, user, unknown_email, cleanup
):
    other = _make_user(cleanup)
    snapshots = []

    # Wrong OTP.
    otp = services.create_password_reset_otp(user.email)
    wrong = "000000" if otp != "000000" else "111111"
    snapshots.append(_snapshot(_post(client, URL_VERIFY, {"email": user.email, "otp": wrong})))
    # Malformed OTP.
    snapshots.append(_snapshot(_post(client, URL_VERIFY, {"email": user.email, "otp": "abc"})))
    # Expired / never issued.
    redis_conn.delete(*services._otp_keys(services._normalize_email(user.email)))
    snapshots.append(_snapshot(_post(client, URL_VERIFY, {"email": user.email, "otp": otp})))
    # Unknown email.
    snapshots.append(_snapshot(_post(client, URL_VERIFY, {"email": unknown_email, "otp": "123456"})))
    # Attempts used up: the right OTP is refused after five wrong tries.
    good = services.create_password_reset_otp(other.email)
    bad = "000000" if good != "000000" else "111111"
    for _ in range(services.OTP_MAX_ATTEMPTS):
        _post(client, URL_VERIFY, {"email": other.email, "otp": bad})
    snapshots.append(_snapshot(_post(client, URL_VERIFY, {"email": other.email, "otp": good})))

    assert all(s == snapshots[0] for s in snapshots)
    assert snapshots[0][0] == 400
    assert "reset_token" not in str(snapshots[0][1])


# ---------------------------------------------------------------- confirm


def test_confirm_success_changes_password_and_token_is_single_use(client, user):
    token = _issue_reset_token(user.email)

    response = _confirm(client, user.email, token)

    assert response.status_code == 200
    assert services.authenticate_user(email=user.email, password=NEW_PASSWORD) is not None
    assert services.authenticate_user(email=user.email, password=OLD_PASSWORD) is None
    with mock.patch.object(LoginRateThrottle, "allow_request", return_value=True):
        login = _post(client, "accounts:login", {"email": user.email, "password": NEW_PASSWORD})
    assert login.status_code == 200

    again = _confirm(client, user.email, token, password="An0ther-Str0ng-Pass!")
    assert again.status_code == 400
    assert services.authenticate_user(email=user.email, password=NEW_PASSWORD) is not None


def test_confirm_is_case_insensitive_on_email(client, user):
    token = _issue_reset_token(user.email)

    response = _confirm(client, user.email.upper(), token)

    assert response.status_code == 200


def test_confirm_token_for_a_different_email_is_rejected(client, user, cleanup):
    other = _make_user(cleanup)
    token = _issue_reset_token(other.email)

    response = _confirm(client, user.email, token)

    assert response.status_code == 400
    assert services.authenticate_user(email=user.email, password=OLD_PASSWORD) is not None
    # The other user's token was not burned by the failed attempt.
    assert _confirm(client, other.email, token).status_code == 200


def test_confirm_failures_are_identical_and_never_reveal_the_account(
    client, user, unknown_email
):
    token = _issue_reset_token(user.email)
    wrong_token = "A" * 43

    unknown = _snapshot(_confirm(client, unknown_email, token))
    bad_token = _snapshot(_confirm(client, user.email, wrong_token))

    assert unknown == bad_token
    assert unknown[0] == 400


def test_confirm_unknown_email_with_weak_password_gets_the_unified_failure(
    client, user, unknown_email
):
    token = _issue_reset_token(user.email)
    baseline = _snapshot(_confirm(client, unknown_email, token))

    weak = _snapshot(_confirm(client, unknown_email, token, password="12345678"))

    assert weak == baseline
    assert weak[0] == 400
    assert "new_password" not in str(weak[1])


def test_confirm_existing_email_wrong_token_weak_password_gets_unified_failure(
    client, user, unknown_email
):
    _issue_reset_token(user.email)
    baseline = _snapshot(_confirm(client, unknown_email, "A" * 43))

    weak = _snapshot(_confirm(client, user.email, "A" * 43, password="12345678"))

    assert weak == baseline
    assert "new_password" not in str(weak[1])


def test_peek_does_not_consume_and_rejects_bad_formats(user, unknown_email):
    token = _issue_reset_token(user.email)

    assert services.peek_password_reset_token(user.email, token) is True
    assert services.peek_password_reset_token(user.email, token) is True
    assert services.peek_password_reset_token(user.email.upper(), token) is True
    assert services.peek_password_reset_token(user.email, "A" * 43) is False
    assert services.peek_password_reset_token(unknown_email, token) is False
    # Malformed values are refused without touching Redis.
    with mock.patch.object(services, "get_redis_connection") as redis_factory:
        assert services.peek_password_reset_token(user.email, "short") is False
        assert services.peek_password_reset_token(user.email, None) is False
        assert services.peek_password_reset_token(None, token) is False
        redis_factory.assert_not_called()
    # Still consumable exactly once after all the peeks.
    assert services.consume_password_reset_token(user.email, token) is True
    assert services.peek_password_reset_token(user.email, token) is False


def test_confirm_weak_password_is_rejected_and_token_stays_valid(client, user):
    token = _issue_reset_token(user.email)

    weak = _confirm(client, user.email, token, password="12345678")

    assert weak.status_code == 400
    assert "new_password" in str(weak.json())
    assert services.authenticate_user(email=user.email, password=OLD_PASSWORD) is not None
    # Same token still works with a strong password.
    assert _confirm(client, user.email, token).status_code == 200


def test_confirm_blacklists_outstanding_refresh_tokens(client, user):
    pair = services.issue_token_pair(user)
    assert OutstandingToken.objects.filter(user=user).count() == 1
    token = _issue_reset_token(user.email)

    assert _confirm(client, user.email, token).status_code == 200

    assert BlacklistedToken.objects.filter(token__user=user).count() == 1
    refreshed = _post(client, "accounts:refresh", {"refresh": pair["refresh"]})
    assert refreshed.status_code == 401


# --------------------------------------------------------------- full flow


def test_full_flow_request_verify_confirm(client, user):
    assert _post(client, URL_REQUEST, {"email": user.email}).status_code == 202
    assert len(mail.outbox) == 1
    otp = re.search(r"\b(\d{6})\b", mail.outbox[0].body).group(1)

    verified = _post(client, URL_VERIFY, {"email": user.email, "otp": otp})
    assert verified.status_code == 200

    confirmed = _confirm(client, user.email, verified.json()["reset_token"])
    assert confirmed.status_code == 200
    assert services.authenticate_user(email=user.email, password=NEW_PASSWORD) is not None
