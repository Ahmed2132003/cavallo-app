"""
Tests for the optional ``locale`` field of POST /api/v1/devices/register/
(Part P-112).
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from devices.models import DeviceToken

pytestmark = pytest.mark.django_db


def _make_user(email):
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type="customer",
    )


def _register(user, **body):
    client = APIClient()
    client.force_authenticate(user=user)
    payload = {"token": "tok-1", "platform": "android"}
    payload.update(body)
    return client.post(reverse("devices:register"), payload, format="json")


@pytest.mark.parametrize(
    "sent, stored",
    [
        ("ar", "ar"),
        ("en", "en"),
        ("ar-EG", "ar"),
        ("EN-us", "en"),
        ("fr", ""),
        ("garbage;;;", ""),
        ("", ""),
    ],
)
def test_locale_is_normalised_and_never_rejected(sent, stored):
    response = _register(_make_user("a@example.com"), locale=sent)

    assert response.status_code == 201
    assert DeviceToken.objects.get(token="tok-1").locale == stored


def test_missing_locale_is_stored_blank_for_older_clients():
    response = _register(_make_user("b@example.com"))

    assert response.status_code == 201
    assert DeviceToken.objects.get(token="tok-1").locale == ""


def test_response_shape_is_unchanged():
    response = _register(_make_user("c@example.com"), locale="ar")

    assert set(response.json()) == {"id", "platform"}


def test_reregistering_updates_the_locale():
    user = _make_user("d@example.com")
    _register(user, locale="ar")

    response = _register(user, locale="en")

    assert response.status_code == 200
    assert DeviceToken.objects.get(token="tok-1").locale == "en"


def test_token_changing_hands_does_not_keep_previous_locale():
    first = _make_user("e@example.com")
    second = _make_user("f@example.com")
    _register(first, locale="ar")

    _register(second)  # older client: no locale

    device = DeviceToken.objects.get(token="tok-1")
    assert device.user_id == second.id
    assert device.locale == ""
