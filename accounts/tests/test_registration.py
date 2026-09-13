"""
API tests for POST /api/v1/auth/register/ (Part P-017).

Goes through real Django URL routing (config/urls.py -> accounts/urls.py
-> RegisterView), not just the serializer/service in isolation, so
these tests also lock in the wiring and confirm errors actually come
back through the custom exception handler (Part P-012) in its
documented envelope shape.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def register_url():
    return reverse("accounts:register")


def _valid_payload(**overrides):
    payload = {
        "email": "newuser@example.com",
        "password": "S0m3-Str0ng-Uncommon-Pass!",
        "password_confirm": "S0m3-Str0ng-Uncommon-Pass!",
        "account_type": "customer",
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestRegisterSuccess:
    def test_customer_registration_returns_201_with_correct_account_type(
        self, api_client, register_url
    ):
        response = api_client.post(
            register_url,
            _valid_payload(email="customer1@example.com", account_type="customer"),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["email"] == "customer1@example.com"
        assert response.data["account_type"] == "customer"
        assert "password" not in response.data
        assert "password_confirm" not in response.data

        user = User.objects.get(email="customer1@example.com")
        assert user.account_type == User.ACCOUNT_TYPE_CUSTOMER
        assert user.check_password("S0m3-Str0ng-Uncommon-Pass!")

    def test_business_registration_returns_201_with_no_premature_profile(
        self, api_client, register_url
    ):
        response = api_client.post(
            register_url,
            _valid_payload(email="biz1@example.com", account_type="business"),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["account_type"] == "business"

        user = User.objects.get(email="biz1@example.com")
        assert user.account_type == User.ACCOUNT_TYPE_BUSINESS
        # This part deliberately does NOT create a BusinessProfile row
        # (that model doesn't exist until Phase 4's P-040) — documented
        # here as an assertion of intent rather than something to check
        # against a model that doesn't exist yet.

    def test_registration_does_not_issue_jwt_tokens(self, api_client, register_url):
        # Login is a separate endpoint (Part P-018) — registration must
        # not auto-login the user for this MVP. See RegisterView's
        # docstring / PROJECT_PROGRESS.md's P-017 entry if this should
        # ever be reconsidered.
        response = api_client.post(
            register_url,
            _valid_payload(email="nojwt@example.com"),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert "access" not in response.data
        assert "refresh" not in response.data

    def test_password_is_stored_hashed_not_plaintext(self, api_client, register_url):
        response = api_client.post(
            register_url,
            _valid_payload(email="hash-check@example.com"),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        user = User.objects.get(email="hash-check@example.com")
        assert user.password != "S0m3-Str0ng-Uncommon-Pass!"


@pytest.mark.django_db
class TestRegisterValidation:
    def test_duplicate_email_returns_400_in_correct_envelope_shape(
        self, api_client, register_url
    ):
        User.objects.create_user(
            username="existing@example.com",
            email="existing@example.com",
            password="Wh4tever-Uncommon-Pass!",
            account_type=User.ACCOUNT_TYPE_CUSTOMER,
        )

        response = api_client.post(
            register_url,
            _valid_payload(email="existing@example.com"),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "VALIDATION_ERROR"
        assert "email" in response.data["error"]["fields"]

    def test_duplicate_email_check_is_case_insensitive(self, api_client, register_url):
        User.objects.create_user(
            username="existing2@example.com",
            email="existing2@example.com",
            password="Wh4tever-Uncommon-Pass!",
            account_type=User.ACCOUNT_TYPE_CUSTOMER,
        )

        response = api_client.post(
            register_url,
            _valid_payload(email="EXISTING2@example.com"),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data["error"]["fields"]

    def test_weak_password_returns_400_with_field_error_and_no_user_created(
        self, api_client, register_url
    ):
        response = api_client.post(
            register_url,
            _valid_payload(
                email="weakpass@example.com",
                password="12345678",
                password_confirm="12345678",
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "VALIDATION_ERROR"
        assert "password" in response.data["error"]["fields"]
        assert not User.objects.filter(email="weakpass@example.com").exists()

    def test_mismatched_password_confirm_returns_400_and_no_user_created(
        self, api_client, register_url
    ):
        response = api_client.post(
            register_url,
            _valid_payload(
                email="mismatch@example.com",
                password="S0m3-Str0ng-Uncommon-Pass!",
                password_confirm="Different-Uncommon-Pass!",
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "password_confirm" in response.data["error"]["fields"]
        assert not User.objects.filter(email="mismatch@example.com").exists()

    def test_missing_required_fields_returns_400_for_each_field(
        self, api_client, register_url
    ):
        response = api_client.post(register_url, {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        fields = response.data["error"]["fields"]
        assert "email" in fields
        assert "password" in fields
        assert "password_confirm" in fields
        assert "account_type" in fields

    def test_invalid_email_format_returns_400(self, api_client, register_url):
        response = api_client.post(
            register_url,
            _valid_payload(email="not-an-email"),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data["error"]["fields"]

    def test_invalid_account_type_returns_400(self, api_client, register_url):
        response = api_client.post(
            register_url,
            _valid_payload(account_type="not-a-real-type"),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "account_type" in response.data["error"]["fields"]
