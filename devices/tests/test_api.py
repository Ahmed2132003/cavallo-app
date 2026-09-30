"""
API tests for Part P-081 - POST /api/v1/devices/register/.

Mirrors social/tests/test_api.py's conventions (force_authenticate,
APIClient, pytest.mark.django_db).
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

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


@pytest.fixture
def api_client():
    return APIClient()


def _url():
    return reverse("devices:register")


class TestRegisterAuth:
    def test_url_is_the_documented_path(self):
        assert _url() == "/api/v1/devices/register/"

    def test_unauthenticated_request_is_rejected(self, api_client):
        response = api_client.post(
            _url(), {"token": "t", "platform": "ios"}, format="json"
        )

        assert response.status_code == 401
        assert DeviceToken.objects.count() == 0


class TestRegisterCreate:
    def test_first_registration_creates_row_for_request_user(self, api_client):
        user = _make_user("p081-create@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.post(
            _url(), {"token": "fcm-token-1", "platform": "android"}, format="json"
        )

        assert response.status_code == 201
        assert DeviceToken.objects.count() == 1
        device = DeviceToken.objects.get()
        assert device.user_id == user.id
        assert device.token == "fcm-token-1"
        assert device.platform == "android"
        assert response.json() == {"id": device.id, "platform": "android"}

    def test_response_does_not_echo_the_token(self, api_client):
        user = _make_user("p081-noecho@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.post(
            _url(), {"token": "secret-token-xyz", "platform": "ios"}, format="json"
        )

        assert "secret-token-xyz" not in response.content.decode()

    def test_one_user_can_register_several_different_tokens(self, api_client):
        user = _make_user("p081-multi@example.com")
        api_client.force_authenticate(user=user)

        api_client.post(
            _url(), {"token": "phone", "platform": "android"}, format="json"
        )
        api_client.post(_url(), {"token": "tablet", "platform": "ios"}, format="json")

        assert DeviceToken.objects.filter(user=user).count() == 2

    def test_token_whitespace_is_trimmed(self, api_client):
        user = _make_user("p081-trim@example.com")
        api_client.force_authenticate(user=user)

        api_client.post(
            _url(), {"token": "  padded-token  ", "platform": "ios"}, format="json"
        )

        assert DeviceToken.objects.get().token == "padded-token"


class TestRegisterUpsert:
    def test_same_user_same_token_is_idempotent(self, api_client):
        user = _make_user("p081-idem@example.com")
        api_client.force_authenticate(user=user)
        payload = {"token": "same-token", "platform": "ios"}

        first = api_client.post(_url(), payload, format="json")
        second = api_client.post(_url(), payload, format="json")

        assert first.status_code == 201
        assert second.status_code == 200
        assert DeviceToken.objects.filter(token="same-token").count() == 1
        assert first.json()["id"] == second.json()["id"]

    def test_different_user_reregistering_takes_ownership(self, api_client):
        user_a = _make_user("p081-owner-a@example.com")
        user_b = _make_user("p081-owner-b@example.com")
        payload = {"token": "shared-device", "platform": "android"}

        api_client.force_authenticate(user=user_a)
        api_client.post(_url(), payload, format="json")
        api_client.force_authenticate(user=user_b)
        response = api_client.post(_url(), payload, format="json")

        assert response.status_code == 200
        assert DeviceToken.objects.filter(token="shared-device").count() == 1
        assert DeviceToken.objects.get(token="shared-device").user_id == user_b.id
        assert user_a.device_tokens.count() == 0
        assert user_b.device_tokens.count() == 1

    def test_platform_is_refreshed_on_reregistration(self, api_client):
        user = _make_user("p081-platform@example.com")
        api_client.force_authenticate(user=user)

        api_client.post(_url(), {"token": "tok", "platform": "android"}, format="json")
        api_client.post(_url(), {"token": "tok", "platform": "ios"}, format="json")

        assert DeviceToken.objects.get(token="tok").platform == "ios"

    def test_owner_comes_from_request_user_never_from_the_body(self, api_client):
        attacker = _make_user("p081-attacker@example.com")
        victim = _make_user("p081-victim@example.com")
        api_client.force_authenticate(user=attacker)

        api_client.post(
            _url(),
            {"token": "idor-token", "platform": "ios", "user": victim.id},
            format="json",
        )

        assert DeviceToken.objects.get(token="idor-token").user_id == attacker.id
        assert victim.device_tokens.count() == 0


class TestRegisterValidation:
    @pytest.mark.parametrize(
        "payload,bad_field",
        [
            ({"platform": "ios"}, "token"),
            ({"token": "", "platform": "ios"}, "token"),
            ({"token": "   ", "platform": "ios"}, "token"),
            ({"token": "x" * 513, "platform": "ios"}, "token"),
            ({"token": "tok"}, "platform"),
            ({"token": "tok", "platform": "windows"}, "platform"),
            ({"token": "tok", "platform": "IOS"}, "platform"),
        ],
    )
    def test_invalid_payload_returns_400_in_the_standard_envelope(
        self, api_client, payload, bad_field
    ):
        user = _make_user("p081-invalid@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.post(_url(), payload, format="json")

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert bad_field in body["error"]["fields"]
        assert DeviceToken.objects.count() == 0
