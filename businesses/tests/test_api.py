"""
API tests for Part P-026 — Business/Customer Profile CRUD.

Uses force_authenticate() (same convention as
accounts/tests/test_permissions.py) since these tests are about
ownership/IDOR/account-type logic, not the JWT login flow itself.
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from businesses.models import BusinessProfile, CustomerProfile
from categories.models import Category

pytestmark = pytest.mark.django_db


def _make_user(account_type: str, email: str) -> User:
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type=account_type,
    )


@pytest.fixture
def api_client():
    return APIClient()


BUSINESS_ME_URL = reverse("businesses:business-me")
CUSTOMER_ME_URL = reverse("customers:customer-me")


def _business_public_url(pk):
    return reverse("businesses:business-public", kwargs={"pk": pk})


class TestBusinessProfileMeCreateAndUpdate:
    def test_business_user_can_create_then_update_own_profile(self, api_client):
        user = _make_user("business", "p026-biz1@example.com")
        api_client.force_authenticate(user=user)

        create_response = api_client.post(
            BUSINESS_ME_URL,
            {
                "business_name": "Acme Trading",
                "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
                "country": "Egypt",
                "city": "Cairo",
            },
            format="json",
        )
        assert create_response.status_code == 201
        assert create_response.json()["business_name"] == "Acme Trading"
        assert create_response.json()["is_verified"] is False
        assert create_response.json()["follower_count"] == 0

        patch_response = api_client.patch(
            BUSINESS_ME_URL,
            {"business_name": "Acme Trading Co.", "city": "Giza"},
            format="json",
        )
        assert patch_response.status_code == 200
        assert patch_response.json()["business_name"] == "Acme Trading Co."
        assert patch_response.json()["city"] == "Giza"

        get_response = api_client.get(BUSINESS_ME_URL)
        assert get_response.status_code == 200
        assert get_response.json()["business_name"] == "Acme Trading Co."
        assert get_response.json()["city"] == "Giza"

    def test_get_me_returns_404_before_onboarding(self, api_client):
        user = _make_user("business", "p026-biz2@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.get(BUSINESS_ME_URL)
        assert response.status_code == 404

    def test_can_set_category_at_onboarding_and_change_it_later(self, api_client):
        user = _make_user("business", "p026-biz3@example.com")
        api_client.force_authenticate(user=user)
        fashion = Category.objects.create(name="Fashion")
        electronics = Category.objects.create(name="Electronics")

        create_response = api_client.post(
            BUSINESS_ME_URL,
            {
                "business_name": "Acme Trading",
                "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
                "country": "Egypt",
                "city": "Cairo",
                "category": fashion.id,
            },
            format="json",
        )
        assert create_response.status_code == 201
        assert create_response.json()["category"] == fashion.id

        patch_response = api_client.patch(
            BUSINESS_ME_URL, {"category": electronics.id}, format="json"
        )
        assert patch_response.status_code == 200
        assert patch_response.json()["category"] == electronics.id

    def test_cannot_create_a_second_profile(self, api_client):
        user = _make_user("business", "p026-biz4@example.com")
        api_client.force_authenticate(user=user)
        payload = {
            "business_name": "Acme Trading",
            "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
            "country": "Egypt",
            "city": "Cairo",
        }
        first = api_client.post(BUSINESS_ME_URL, payload, format="json")
        assert first.status_code == 201

        second = api_client.post(BUSINESS_ME_URL, payload, format="json")
        assert second.status_code == 400


class TestBusinessProfileIDOR:
    """The core test proving the structural IDOR mitigation works —
    not just a permission-class check (architecture Section 5, rule
    10)."""

    def test_patch_ignores_id_field_and_updates_only_own_profile(self, api_client):
        user_a = _make_user("business", "p026-idor-a@example.com")
        user_b = _make_user("business", "p026-idor-b@example.com")

        client_b = APIClient()
        client_b.force_authenticate(user=user_b)
        profile_b = client_b.post(
            BUSINESS_ME_URL,
            {
                "business_name": "Business B",
                "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
                "country": "Egypt",
                "city": "Alexandria",
            },
            format="json",
        ).json()

        api_client.force_authenticate(user=user_a)
        create_a = api_client.post(
            BUSINESS_ME_URL,
            {
                "business_name": "Business A",
                "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
                "country": "Egypt",
                "city": "Cairo",
            },
            format="json",
        )
        assert create_a.status_code == 201
        profile_a_id = create_a.json()["id"]

        # User A sends B's profile id in the PATCH body. The endpoint
        # must silently ignore it and only ever touch A's own profile.
        malicious_patch = api_client.patch(
            BUSINESS_ME_URL,
            {"id": profile_b["id"], "business_name": "Hijacked Name"},
            format="json",
        )
        assert malicious_patch.status_code == 200
        assert malicious_patch.json()["id"] == profile_a_id

        # A's own profile was updated...
        BusinessProfile.objects.get(pk=profile_a_id).refresh_from_db()
        assert (
            BusinessProfile.objects.get(pk=profile_a_id).business_name
            == "Hijacked Name"
        )
        # ...and B's profile is completely untouched.
        assert (
            BusinessProfile.objects.get(pk=profile_b["id"]).business_name
            == "Business B"
        )


class TestBusinessProfilePublicView:
    def test_public_view_accessible_without_auth(self, api_client):
        owner = _make_user("business", "p026-pub1@example.com")
        profile = BusinessProfile.objects.create(
            user=owner,
            business_name="Public Biz",
            business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
            country="Egypt",
            city="Cairo",
        )

        # No credentials/auth header set at all.
        response = api_client.get(_business_public_url(profile.id))
        assert response.status_code == 200
        assert response.json()["business_name"] == "Public Biz"

    def test_me_endpoint_unauthenticated_fails_with_401(self, api_client):
        response = api_client.get(BUSINESS_ME_URL)
        assert response.status_code == 401


class TestBusinessProfileAccountTypeGuard:
    def test_customer_type_user_cannot_create_business_profile(self, api_client):
        user = _make_user("customer", "p026-cust-guard1@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.post(
            BUSINESS_ME_URL,
            {
                "business_name": "Should Not Work",
                "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
                "country": "Egypt",
                "city": "Cairo",
            },
            format="json",
        )
        assert response.status_code == 400
        assert BusinessProfile.objects.filter(user=user).exists() is False

    def test_customer_type_user_cannot_patch_business_me(self, api_client):
        # A Customer user has no BusinessProfile at all, so PATCH must
        # 404 (there is nothing to update) rather than ever reaching
        # the service layer's account_type guard for this method.
        user = _make_user("customer", "p026-cust-guard2@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.patch(
            BUSINESS_ME_URL, {"business_name": "Nope"}, format="json"
        )
        assert response.status_code == 404


class TestCustomerProfileMe:
    def test_customer_user_can_create_then_update_own_profile(self, api_client):
        user = _make_user("customer", "p026-cust1@example.com")
        api_client.force_authenticate(user=user)

        create_response = api_client.post(
            CUSTOMER_ME_URL,
            {"display_name": "John Doe", "country": "Egypt", "city": "Ismailia"},
            format="json",
        )
        assert create_response.status_code == 201

        patch_response = api_client.patch(
            CUSTOMER_ME_URL, {"city": "Cairo"}, format="json"
        )
        assert patch_response.status_code == 200
        assert patch_response.json()["city"] == "Cairo"

    def test_get_me_returns_404_before_onboarding(self, api_client):
        user = _make_user("customer", "p026-cust2@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.get(CUSTOMER_ME_URL)
        assert response.status_code == 404

    def test_idor_patch_ignores_id_field(self, api_client):
        user_a = _make_user("customer", "p026-cust-idor-a@example.com")
        user_b = _make_user("customer", "p026-cust-idor-b@example.com")

        client_b = APIClient()
        client_b.force_authenticate(user=user_b)
        profile_b = client_b.post(
            CUSTOMER_ME_URL,
            {"display_name": "Customer B", "country": "Egypt", "city": "Cairo"},
            format="json",
        ).json()

        api_client.force_authenticate(user=user_a)
        create_a = api_client.post(
            CUSTOMER_ME_URL,
            {"display_name": "Customer A", "country": "Egypt", "city": "Cairo"},
            format="json",
        )
        profile_a_id = create_a.json()["id"]

        malicious_patch = api_client.patch(
            CUSTOMER_ME_URL,
            {"id": profile_b["id"], "display_name": "Hijacked"},
            format="json",
        )
        assert malicious_patch.status_code == 200
        assert malicious_patch.json()["id"] == profile_a_id
        assert (
            CustomerProfile.objects.get(pk=profile_b["id"]).display_name == "Customer B"
        )

    def test_business_type_user_cannot_create_customer_profile(self, api_client):
        user = _make_user("business", "p026-biz-guard1@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.post(
            CUSTOMER_ME_URL,
            {"display_name": "Should Not Work", "country": "Egypt", "city": "Cairo"},
            format="json",
        )
        assert response.status_code == 400
        assert CustomerProfile.objects.filter(user=user).exists() is False
