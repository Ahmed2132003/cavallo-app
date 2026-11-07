"""
Tests for Task P2.7b - login timing equalization
(accounts.services.authenticate_user).

An unknown email used to return before any password hash ran, so the response
time revealed which emails are registered. These tests assert that the hash
now runs on that path too. They never measure time (no timing assertions, no
sleeping): they only check which hashing code was called. The response shape,
status and messages must be unchanged. All data is fake.
"""

from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient

from accounts import services

User = get_user_model()

pytestmark = pytest.mark.django_db

RAW_PASSWORD = "S0m3-Str0ng-Uncommon-Pass!"
UNKNOWN_EMAIL = "nobody-registered@example.com"

# Only the login throttle keys are removed (never cache.clear(), which would
# also flush the password reset OTP/token keys that share this Redis DB).
_LOGIN_THROTTLE_PATTERN = "throttle_login_*"


@pytest.fixture(autouse=True)
def _clean_login_throttle_cache():
    cache.delete_pattern(_LOGIN_THROTTLE_PATTERN)
    yield
    cache.delete_pattern(_LOGIN_THROTTLE_PATTERN)


@pytest.fixture
def user():
    return User.objects.create_user(
        username="timing.user@example.com",
        email="timing.user@example.com",
        password=RAW_PASSWORD,
        account_type=User.ACCOUNT_TYPE_CUSTOMER,
    )


# ------------------------------------------------------------ service level


def test_unknown_email_runs_the_dummy_hash_and_returns_none():
    with mock.patch.object(User, "set_password") as set_password:
        result = services.authenticate_user(email=UNKNOWN_EMAIL, password=RAW_PASSWORD)

    assert result is None
    set_password.assert_called_once_with(RAW_PASSWORD)


def test_unknown_email_really_goes_through_the_password_hasher():
    # Not just "set_password was called": the hashing function itself ran,
    # with the submitted password.
    with mock.patch(
        "django.contrib.auth.base_user.make_password", wraps=make_password
    ) as hasher:
        result = services.authenticate_user(email=UNKNOWN_EMAIL, password=RAW_PASSWORD)

    assert result is None
    hasher.assert_called_once_with(RAW_PASSWORD)


@pytest.mark.parametrize(
    "password, should_authenticate",
    [(RAW_PASSWORD, True), ("totally-wrong-password", False)],
)
def test_known_email_does_not_get_an_extra_dummy_hash(
    user, password, should_authenticate
):
    # The real check (authenticate -> check_password) is the hash on this
    # path, so the dummy one must not run as well.
    with mock.patch.object(User, "set_password") as set_password:
        result = services.authenticate_user(email=user.email, password=password)

    assert (result is not None) is should_authenticate
    set_password.assert_not_called()


def test_inactive_user_is_hashed_by_the_backend_and_still_rejected(user):
    # No early return skips the hash for an inactive account either:
    # ModelBackend checks the password before it checks is_active.
    user.is_active = False
    user.save(update_fields=["is_active"])
    original = User.check_password

    with mock.patch.object(
        User, "check_password", autospec=True, side_effect=original
    ) as check_password, mock.patch.object(User, "set_password") as set_password:
        result = services.authenticate_user(email=user.email, password=RAW_PASSWORD)

    assert result is None
    check_password.assert_called_once()
    set_password.assert_not_called()


# ------------------------------------------------------------- HTTP level


def test_login_endpoint_reaches_the_dummy_hash_for_an_unknown_email():
    client = APIClient()

    with mock.patch.object(User, "set_password") as set_password:
        response = client.post(
            reverse("accounts:login"),
            {"email": UNKNOWN_EMAIL, "password": RAW_PASSWORD},
            format="json",
        )

    assert response.status_code == 401
    set_password.assert_called_once_with(RAW_PASSWORD)


def test_unknown_email_and_wrong_password_still_get_the_same_response(user):
    client = APIClient()
    url = reverse("accounts:login")

    unknown = client.post(
        url, {"email": UNKNOWN_EMAIL, "password": RAW_PASSWORD}, format="json"
    )
    wrong = client.post(
        url, {"email": user.email, "password": "totally-wrong-password"}, format="json"
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()
    error = unknown.json()["error"]
    assert error["code"] == "AUTHENTICATION_FAILED"
    assert error["message"] == "Unable to log in with the provided credentials."
