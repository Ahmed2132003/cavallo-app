"""
Model tests for Part P-081 - DeviceToken.
"""

import pytest
from django.db import IntegrityError, transaction

from accounts.models import User
from devices.models import DeviceToken

pytestmark = pytest.mark.django_db


def _make_user(email: str) -> User:
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type="customer",
    )


def test_platform_choices_are_ios_and_android_only():
    assert [value for value, _label in DeviceToken.PLATFORM_CHOICES] == [
        "ios",
        "android",
    ]


def test_token_is_globally_unique():
    user_a = _make_user("p081-model-a@example.com")
    user_b = _make_user("p081-model-b@example.com")
    DeviceToken.objects.create(user=user_a, token="dup-token", platform="ios")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            DeviceToken.objects.create(user=user_b, token="dup-token", platform="ios")

    assert DeviceToken.objects.filter(token="dup-token").count() == 1


def test_user_can_own_many_tokens():
    user = _make_user("p081-model-many@example.com")
    DeviceToken.objects.create(user=user, token="tok-1", platform="ios")
    DeviceToken.objects.create(user=user, token="tok-2", platform="android")

    assert user.device_tokens.count() == 2


def test_deleting_user_deletes_their_tokens():
    user = _make_user("p081-model-cascade@example.com")
    DeviceToken.objects.create(user=user, token="tok-cascade", platform="android")

    user.delete()

    assert DeviceToken.objects.filter(token="tok-cascade").count() == 0


def test_str_never_contains_the_full_token():
    user = _make_user("p081-model-str@example.com")
    secret = "abcdefgh-this-part-must-not-appear-in-str"
    device = DeviceToken.objects.create(user=user, token=secret, platform="ios")

    text = str(device)

    assert "abcdefgh" in text
    assert secret not in text
