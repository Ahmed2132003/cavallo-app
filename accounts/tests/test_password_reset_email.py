"""
Focused tests for Task P2.3 - password reset OTP email delivery.

Like the P2.1/P2.2 tests, these talk to the real Redis configured for the
test settings. Each test uses a unique email address, and the fixture deletes
only the keys derived from that address afterwards. The database is never
flushed. Email goes through Django's locmem backend (mail.outbox).
"""

import logging
import re
import smtplib
from unittest import mock
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django_redis import get_redis_connection

from accounts import services
from accounts.services import (
    OTP_TTL_SECONDS,
    request_password_reset_otp,
    verify_password_reset_otp,
)

User = get_user_model()

pytestmark = pytest.mark.django_db

TEST_PASSWORD = "S0m3-Str0ng-Uncommon-Pass!"


@pytest.fixture
def redis_conn():
    return get_redis_connection("default")


@pytest.fixture
def registered_email():
    # Mixed case on purpose: the stored address differs from what the
    # tests usually type.
    return f"Mixed.Case-{uuid4().hex}@Example.com"


@pytest.fixture
def user(registered_email, redis_conn):
    created = User.objects.create_user(
        username=registered_email,
        email=registered_email,
        password=TEST_PASSWORD,
    )
    yield created
    redis_conn.delete(*services._otp_keys(services._normalize_email(registered_email)))


@pytest.fixture
def unknown_email(redis_conn):
    address = f"nobody-{uuid4().hex}@example.com"
    yield address
    redis_conn.delete(*services._otp_keys(services._normalize_email(address)))


def _keys(email):
    return services._otp_keys(services._normalize_email(email))


def _email_id_keys(redis_conn, email):
    email_id = _keys(email)[0].rsplit(":", 1)[1]
    return sorted(k.decode() for k in redis_conn.scan_iter(match=f"*{email_id}*"))


def _otp_from(message):
    found = re.findall(r"(?<![0-9])[0-9]{6}(?![0-9])", message.body)
    assert len(found) == 1
    return found[0]


def test_existing_active_user_gets_exactly_one_email(user, mailoutbox):
    result = request_password_reset_otp(user.email)

    assert result is None
    assert len(mailoutbox) == 1
    message = mailoutbox[0]
    assert message.to == [user.email]
    otp = _otp_from(message)
    assert verify_password_reset_otp(user.email, otp) is True


def test_email_matching_is_case_insensitive(user, mailoutbox):
    typed = user.email.swapcase()
    assert typed != user.email

    request_password_reset_otp(f"  {typed}  ")

    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [user.email]
    assert verify_password_reset_otp(typed, _otp_from(mailoutbox[0])) is True


def test_unknown_email_sends_nothing_and_creates_no_keys(
    unknown_email, redis_conn, mailoutbox
):
    result = request_password_reset_otp(unknown_email)

    assert result is None
    assert mailoutbox == []
    assert _email_id_keys(redis_conn, unknown_email) == []


def test_visible_result_is_identical_for_all_cases(
    user, unknown_email, registered_email, mailoutbox
):
    existing = request_password_reset_otp(user.email)
    unknown = request_password_reset_otp(unknown_email)

    User.objects.filter(pk=user.pk).update(is_active=False)
    inactive = request_password_reset_otp(user.email)

    User.objects.filter(pk=user.pk).update(is_active=True)
    with mock.patch(
        "accounts.services.send_mail", side_effect=smtplib.SMTPException("boom")
    ):
        failed = request_password_reset_otp(user.email)

    assert existing is None
    assert unknown is None
    assert inactive is None
    assert failed is None


def test_inactive_user_sends_nothing_and_creates_no_keys(user, redis_conn, mailoutbox):
    User.objects.filter(pk=user.pk).update(is_active=False)

    result = request_password_reset_otp(user.email)

    assert result is None
    assert mailoutbox == []
    assert _email_id_keys(redis_conn, user.email) == []


@pytest.mark.parametrize("bad", ["", "   ", None, 123, b"x@example.com"])
def test_malformed_input_is_a_silent_no_op(bad, mailoutbox):
    assert request_password_reset_otp(bad) is None
    assert mailoutbox == []


def test_ambiguous_email_sends_nothing(user, redis_conn, mailoutbox):
    with mock.patch.object(services.User.objects, "filter", return_value=[user, user]):
        result = request_password_reset_otp(user.email)

    assert result is None
    assert mailoutbox == []
    assert _email_id_keys(redis_conn, user.email) == []


@pytest.mark.parametrize(
    "exc_class", [smtplib.SMTPException, smtplib.SMTPRecipientsRefused, OSError]
)
def test_send_failure_is_swallowed_invalidates_otp_and_does_not_log_secrets(
    exc_class, user, redis_conn, mailoutbox, caplog
):
    caplog.set_level(logging.DEBUG)
    captured = {}

    def _failing_send(**kwargs):
        captured["otp"] = re.search(r"[0-9]{6}", kwargs["message"]).group(0)
        raise exc_class(f"{kwargs['recipient_list']} {kwargs['message']}")

    with mock.patch("accounts.services.send_mail", side_effect=_failing_send):
        result = request_password_reset_otp(user.email)

    assert result is None
    assert mailoutbox == []
    otp = captured["otp"]
    # The OTP was invalidated: its keys are gone and it can not verify.
    assert _email_id_keys(redis_conn, user.email) == []
    assert verify_password_reset_otp(user.email, otp) is False
    # The failure was logged, without the OTP, the address or the error text.
    assert "failed" in caplog.text
    assert otp not in caplog.text
    assert user.email not in caplog.text
    assert user.email.lower() not in caplog.text
    assert user.email.split("@")[0] not in caplog.text


def test_otp_exists_only_in_the_email_body(user, redis_conn, mailoutbox, caplog):
    caplog.set_level(logging.DEBUG)

    result = request_password_reset_otp(user.email)

    otp = _otp_from(mailoutbox[0])
    # Return value.
    assert result is None
    # Logs.
    assert otp not in caplog.text
    # Raw Redis: only the two P2.1 keys exist for this address, holding the
    # issuance-id:digest value and an integer counter, never the OTP itself.
    otp_key, attempts_key = _keys(user.email)
    assert _email_id_keys(redis_conn, user.email) == sorted([otp_key, attempts_key])
    stored = redis_conn.get(otp_key).decode("ascii")
    assert re.fullmatch(r"[0-9a-f]{32}:[0-9a-f]{64}", stored)
    assert stored != otp
    assert redis_conn.get(attempts_key).decode("ascii").isdigit()
    # Subject and sender data.
    assert otp not in mailoutbox[0].subject
    assert otp not in mailoutbox[0].from_email


def test_second_request_invalidates_previous_otp_and_sends_new_email(user, mailoutbox):
    request_password_reset_otp(user.email)
    request_password_reset_otp(user.email)

    assert len(mailoutbox) == 2
    first_otp = _otp_from(mailoutbox[0])
    second_otp = _otp_from(mailoutbox[1])
    if first_otp != second_otp:
        assert verify_password_reset_otp(user.email, first_otp) is False
    assert verify_password_reset_otp(user.email, second_otp) is True


def test_subject_is_fixed_and_has_no_otp(user, mailoutbox):
    request_password_reset_otp(user.email)

    message = mailoutbox[0]
    assert message.subject == services._OTP_EMAIL_SUBJECTS["ar"]
    assert not re.search(r"[0-9]{6}", message.subject)


def test_default_language_is_arabic_and_body_is_complete(user, mailoutbox):
    request_password_reset_otp(user.email)

    message = mailoutbox[0]
    otp = _otp_from(message)
    assert message.body == services._OTP_EMAIL_BODIES["ar"].format(
        otp=otp, ttl=OTP_TTL_SECONDS
    )


def test_english_language_user_gets_english_email(user, mailoutbox):
    User.objects.filter(pk=user.pk).update(preferred_language="en")

    request_password_reset_otp(user.email)

    message = mailoutbox[0]
    assert message.subject == services._OTP_EMAIL_SUBJECTS["en"]
    assert "If you did not request this, ignore this email." in message.body
    assert f"{OTP_TTL_SECONDS} seconds" in message.body


def test_unknown_language_falls_back_to_arabic(user, mailoutbox):
    User.objects.filter(pk=user.pk).update(preferred_language="xx")

    request_password_reset_otp(user.email)

    assert mailoutbox[0].subject == services._OTP_EMAIL_SUBJECTS["ar"]


def test_validity_in_body_follows_otp_ttl_constant(user, mailoutbox):
    with mock.patch("accounts.services.OTP_TTL_SECONDS", 120):
        request_password_reset_otp(user.email)

    assert "120" in mailoutbox[0].body


def test_sender_is_default_from_email(user, mailoutbox, settings):
    settings.DEFAULT_FROM_EMAIL = "noreply@example.test"

    request_password_reset_otp(user.email)

    assert mailoutbox[0].from_email == "noreply@example.test"