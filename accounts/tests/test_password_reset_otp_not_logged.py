"""
The raw OTP must exist only in the email body: never in any log record,
on a successful send or on a failed one.
"""

import logging
import smtplib
from unittest import mock
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django_redis import get_redis_connection

from accounts import services

User = get_user_model()

pytestmark = pytest.mark.django_db

FIXED_OTP = "483920"


@pytest.fixture
def user():
    address = f"otp-log-{uuid4().hex}@example.com"
    created = User.objects.create_user(
        username=address, email=address, password="S0m3-Str0ng-Uncommon-Pass!"
    )
    yield created
    get_redis_connection("default").delete(
        *services._otp_keys(services._normalize_email(address))
    )


def test_otp_in_email_body_but_not_in_logs(user, mailoutbox, caplog):
    caplog.set_level(logging.DEBUG)
    with mock.patch.object(
        services, "create_password_reset_otp", return_value=FIXED_OTP
    ):
        services.request_password_reset_otp(user.email)

    assert len(mailoutbox) == 1
    assert FIXED_OTP in mailoutbox[0].body
    assert FIXED_OTP not in caplog.text


def test_otp_not_logged_when_sending_fails(user, caplog):
    caplog.set_level(logging.DEBUG)
    with mock.patch.object(
        services, "create_password_reset_otp", return_value=FIXED_OTP
    ), mock.patch(
        "accounts.services.send_mail", side_effect=smtplib.SMTPException(FIXED_OTP)
    ):
        services.request_password_reset_otp(user.email)

    assert FIXED_OTP not in caplog.text
    assert user.email not in caplog.text
