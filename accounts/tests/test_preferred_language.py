"""Tests for User.preferred_language and GET/PATCH /api/v1/auth/me/ (P-112)."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()

pytestmark = pytest.mark.django_db

ME_URL = "/api/v1/auth/me/"


@pytest.fixture
def user():
    return User.objects.create_user(
        username="lang@example.com",
        email="lang@example.com",
        password="StrongPass123!",
    )


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


def test_new_user_defaults_to_arabic(user):
    assert user.preferred_language == "ar"


def test_me_get_exposes_preferred_language(client):
    response = client.get(ME_URL)

    assert response.status_code == 200
    assert response.json()["preferred_language"] == "ar"


def test_me_get_still_returns_the_original_fields(client, user):
    body = client.get(ME_URL).json()

    assert body["id"] == user.id
    assert body["email"] == "lang@example.com"
    assert body["account_type"] == "customer"
    assert body["is_moderator"] is False
    assert body["is_staff"] is False


def test_patch_changes_language_and_persists(client, user):
    response = client.patch(ME_URL, {"preferred_language": "en"}, format="json")

    assert response.status_code == 200
    assert response.json()["preferred_language"] == "en"
    user.refresh_from_db()
    assert user.preferred_language == "en"

    back = client.patch(ME_URL, {"preferred_language": "ar"}, format="json")
    assert back.status_code == 200
    user.refresh_from_db()
    assert user.preferred_language == "ar"


def test_patch_rejects_unsupported_language(client, user):
    response = client.patch(ME_URL, {"preferred_language": "fr"}, format="json")

    assert response.status_code == 400
    user.refresh_from_db()
    assert user.preferred_language == "ar"


def test_patch_ignores_fields_the_user_may_not_change(client, user):
    response = client.patch(
        ME_URL,
        {
            "preferred_language": "en",
            "is_staff": True,
            "is_moderator": True,
            "account_type": "business",
        },
        format="json",
    )

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.preferred_language == "en"
    assert user.is_staff is False
    assert user.is_moderator is False
    assert user.account_type == "customer"


def test_patch_with_empty_body_changes_nothing(client, user):
    response = client.patch(ME_URL, {}, format="json")

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.preferred_language == "ar"


def test_patch_requires_authentication():
    response = APIClient().patch(ME_URL, {"preferred_language": "en"}, format="json")

    assert response.status_code == 401
