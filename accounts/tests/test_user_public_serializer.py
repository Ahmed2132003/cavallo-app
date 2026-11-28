"""
Tests for accounts.serializers.UserPublicSerializer (Part P-116A-2).

Storage: the default storage is swapped for Django's InMemoryStorage for
each test (same approach as test_profile_avatar.py), so no MinIO/S3 is
needed.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from rest_framework.test import APIRequestFactory

from accounts.serializers import UserPublicSerializer
from businesses.models import BusinessProfile, CustomerProfile

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _in_memory_storage(settings):
    storages = dict(settings.STORAGES)
    storages["default"] = {
        "BACKEND": "django.core.files.storage.InMemoryStorage",
        "OPTIONS": {"base_url": "/media/"},
    }
    settings.STORAGES = storages
    yield


def _make_user(name="pub", **extra):
    return User.objects.create_user(
        username=f"{name}-user",
        email=f"{name}@example.com",
        password="StrongPass123!",
        **extra,
    )


def _with_avatar(user):
    user.avatar.save("face.png", ContentFile(b"not-really-a-png"), save=True)
    return user


def _request():
    return APIRequestFactory().get("/")


def _data(user, with_request=True):
    context = {"request": _request()} if with_request else {}
    return UserPublicSerializer(user, context=context).data


def test_only_display_name_and_avatar_keys():
    user = _make_user(display_name="Mona")
    assert set(_data(user).keys()) == {"display_name", "avatar"}


def test_avatar_is_absolute_url_when_set():
    user = _with_avatar(_make_user())
    avatar = _data(user)["avatar"]
    assert avatar.startswith("http://testserver/media/avatars/")


def test_avatar_is_null_when_not_set():
    assert _data(_make_user())["avatar"] is None


def test_no_request_in_context_returns_null_avatar_and_does_not_crash():
    user = _with_avatar(_make_user(display_name="Mona"))
    data = _data(user, with_request=False)
    assert data["avatar"] is None
    assert data["display_name"] == "Mona"


def test_display_name_prefers_user_display_name_over_profiles():
    user = _make_user(display_name="  Mona  ")
    CustomerProfile.objects.create(user=user, display_name="Profile Mona")
    assert _data(user)["display_name"] == "Mona"


def test_display_name_falls_back_to_business_name():
    user = _make_user(account_type="business")
    BusinessProfile.objects.create(
        user=user,
        business_name="Nile Textiles",
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )
    assert _data(user)["display_name"] == "Nile Textiles"


def test_display_name_falls_back_to_customer_profile_name():
    user = _make_user()
    CustomerProfile.objects.create(user=user, display_name="Mona Ali")
    assert _data(user)["display_name"] == "Mona Ali"


def test_user_without_any_profile_works_and_never_exposes_identity():
    user = _make_user(first_name="Omar", last_name="Hassan")
    data = _data(user)
    assert data == {"display_name": "", "avatar": None}
    assert user.email not in str(data)
    assert user.username not in str(data)


def test_blank_user_display_name_with_blank_profile_names_is_empty_string():
    user = _make_user(display_name="   ")
    CustomerProfile.objects.create(user=user, display_name="")
    assert _data(user)["display_name"] == ""
