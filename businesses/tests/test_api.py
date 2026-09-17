"""
API tests for Part P-026 — Business/Customer Profile CRUD.

Uses force_authenticate() (same convention as
accounts/tests/test_permissions.py) since these tests are about
ownership/IDOR/account-type logic, not the JWT login flow itself.
"""

import pytest
from django.core.cache import cache
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


class TestBusinessProfilePhoneNumber:
    """
    Part P-027, API-integration level. test_serializers.py already
    covers validate_phone_number() in isolation via
    serializer.is_valid() — these tests instead go through the real
    view + services.create_business_profile()/update_business_profile()
    call path, which is what actually caught this part's real bug
    during manual verification: create_business_profile()'s original
    signature had no phone_number parameter at all, so a POST /me/
    that included phone_number raised an unhandled TypeError (500),
    not a clean 400 - pytest alone (serializer-only tests) did not
    catch this, only a live request through the full stack did.
    """

    def test_can_onboard_with_a_valid_phone_number(self, api_client):
        user = _make_user("business", "p027-biz1@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.post(
            BUSINESS_ME_URL,
            {
                "business_name": "Acme Trading",
                "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
                "country": "Egypt",
                "city": "Cairo",
                "phone_number": "+20 100 123 4567",
            },
            format="json",
        )
        assert response.status_code == 201
        # Normalized to E.164, matching serializers.py's
        # validate_phone_number() contract.
        assert response.json()["phone_number"] == "+201001234567"

    def test_can_onboard_with_no_phone_number_at_all(self, api_client):
        # phone_number is optional - onboarding must still succeed
        # without it (this is the exact "no phone" flow that must
        # keep working, not just the "has a phone" flow).
        user = _make_user("business", "p027-biz2@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.post(
            BUSINESS_ME_URL,
            {
                "business_name": "No Phone Co",
                "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
                "country": "Egypt",
                "city": "Giza",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["phone_number"] == ""

    def test_can_patch_phone_number_after_onboarding(self, api_client):
        user = _make_user("business", "p027-biz3@example.com")
        api_client.force_authenticate(user=user)
        api_client.post(
            BUSINESS_ME_URL,
            {
                "business_name": "Acme Trading",
                "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
                "country": "Egypt",
                "city": "Cairo",
            },
            format="json",
        )

        response = api_client.patch(
            BUSINESS_ME_URL, {"phone_number": "+966501234567"}, format="json"
        )
        assert response.status_code == 200
        assert response.json()["phone_number"] == "+966501234567"

    def test_invalid_phone_number_is_rejected_and_previous_value_survives(
        self, api_client
    ):
        user = _make_user("business", "p027-biz4@example.com")
        api_client.force_authenticate(user=user)
        api_client.post(
            BUSINESS_ME_URL,
            {
                "business_name": "Acme Trading",
                "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
                "country": "Egypt",
                "city": "Cairo",
                "phone_number": "+966501234567",
            },
            format="json",
        )

        # No "+" country code - must be rejected with 400, not silently
        # assumed to be an Egyptian local number.
        response = api_client.patch(
            BUSINESS_ME_URL, {"phone_number": "01001234567"}, format="json"
        )
        assert response.status_code == 400
        assert "phone_number" in response.json()["error"]["fields"]

        # The rejected PATCH must not have touched the previously
        # stored valid value.
        get_response = api_client.get(BUSINESS_ME_URL)
        assert get_response.json()["phone_number"] == "+966501234567"


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


class TestBusinessProfilePublicCaching:
    """
    Part P-030, read-side only. This class does not test invalidation
    (that is BusinessProfileMeView.patch()'s own cache.delete() call,
    added in this Part's next step) - only that a second read within
    the 5-minute TTL is served from Redis instead of hitting Postgres
    again.

    A genuine cache miss costs 2 queries, not 1: BusinessProfileSerializer's
    is_verified field reads through BusinessProfile.is_verified, a
    Python-level property (pre-existing, from P-024/P-026 - see
    businesses/models.py) that reads obj.user.is_business_verified.
    BusinessProfilePublicView's queryset has no select_related("user"),
    so that property access issues its own separate SELECT against
    accounts_user in addition to the BusinessProfile SELECT itself.
    That second query is pre-existing behavior, unrelated to and
    unchanged by this Part - asserting 2 (not 1) here documents it
    rather than silently tolerating a wrong assumption. The one
    Part P-030 acceptance criterion this class actually proves is the
    second number: a cache HIT costs 0 queries.
    """

    def setup_method(self, _):
        # Clean cache DB per test - same reasoning as
        # core/tests/test_cache.py (P-014): avoids cross-test leakage
        # on the shared Redis cache DB.
        cache.clear()

    def test_second_read_within_ttl_does_not_hit_db(
        self, api_client, django_assert_num_queries
    ):
        owner = _make_user("business", "p030-cache1@example.com")
        profile = BusinessProfile.objects.create(
            user=owner,
            business_name="Cached Biz",
            business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
            country="Egypt",
            city="Cairo",
        )
        url = _business_public_url(profile.id)

        # Cache miss: 1 query for the BusinessProfile row, 1 for the
        # related User row read by the is_verified property - see this
        # class's own docstring for why 2 (not 1) is correct here.
        with django_assert_num_queries(2):
            first = api_client.get(url)
        assert first.status_code == 200
        assert first.json()["business_name"] == "Cached Biz"

        # Cache hit: the full serialized payload was stored on the miss
        # above, so this second read touches the database zero times.
        with django_assert_num_queries(0):
            second = api_client.get(url)
        assert second.status_code == 200
        assert second.json() == first.json()

    def test_different_business_ids_are_cached_independently(
        self, api_client, django_assert_num_queries
    ):
        owner_1 = _make_user("business", "p030-cache2@example.com")
        owner_2 = _make_user("business", "p030-cache3@example.com")
        profile_1 = BusinessProfile.objects.create(
            user=owner_1,
            business_name="Biz One",
            business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
            country="Egypt",
            city="Cairo",
        )
        profile_2 = BusinessProfile.objects.create(
            user=owner_2,
            business_name="Biz Two",
            business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
            country="Egypt",
            city="Giza",
        )

        # Warm profile_1's cache entry only.
        api_client.get(_business_public_url(profile_1.id))

        # profile_2 must still be a genuine cache miss (its own key,
        # 2 queries per this class's docstring), not accidentally
        # served from profile_1's cached entry (which would be 0).
        with django_assert_num_queries(2):
            response = api_client.get(_business_public_url(profile_2.id))
        assert response.status_code == 200
        assert response.json()["business_name"] == "Biz Two"


class TestBusinessProfilePublicCacheInvalidation:
    """
    Part P-030, write-side. Proves BusinessProfileMeView.patch()'s
    cache.delete() call actually fires on a real request - not just
    that the line exists in the source. Per this Part's own Testing
    note: "owner PATCHes their profile via /me/, then immediately
    GETs the public /businesses/{id}/ endpoint - confirm the updated
    value is returned, not a stale cached one".
    """

    def setup_method(self, _):
        cache.clear()

    def test_owner_patch_invalidates_the_public_read_cache(self, api_client):
        user = _make_user("business", "p030-invalidate1@example.com")
        api_client.force_authenticate(user=user)

        create_response = api_client.post(
            BUSINESS_ME_URL,
            {
                "business_name": "Original Name",
                "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
                "country": "Egypt",
                "city": "Cairo",
            },
            format="json",
        )
        assert create_response.status_code == 201
        profile_id = create_response.json()["id"]
        public_url = _business_public_url(profile_id)

        # Warm the public-read cache entry with the pre-update name.
        warm_response = api_client.get(public_url)
        assert warm_response.status_code == 200
        assert warm_response.json()["business_name"] == "Original Name"

        # Owner updates their own profile via PATCH /me/.
        patch_response = api_client.patch(
            BUSINESS_ME_URL, {"business_name": "Updated Name"}, format="json"
        )
        assert patch_response.status_code == 200

        # The very next public read - still well within the 5-minute
        # TTL - must return the fresh value, not the cached stale one.
        # This is what actually proves cache.delete() fired: if it
        # hadn't, this would still return "Original Name".
        after_patch_response = api_client.get(public_url)
        assert after_patch_response.status_code == 200
        assert after_patch_response.json()["business_name"] == "Updated Name"

    def test_patch_that_changes_no_watched_field_still_invalidates(self, api_client):
        # Deliberately unconditional: cache.delete() runs on every
        # successful PATCH regardless of which field changed, so a
        # stale cached response is never served after ANY update -
        # not just ones that happen to touch business_name.
        user = _make_user("business", "p030-invalidate2@example.com")
        api_client.force_authenticate(user=user)

        create_response = api_client.post(
            BUSINESS_ME_URL,
            {
                "business_name": "Same Name Throughout",
                "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
                "country": "Egypt",
                "city": "Cairo",
            },
            format="json",
        )
        profile_id = create_response.json()["id"]
        public_url = _business_public_url(profile_id)

        api_client.get(public_url)  # warm the cache

        patch_response = api_client.patch(
            BUSINESS_ME_URL, {"city": "Alexandria"}, format="json"
        )
        assert patch_response.status_code == 200

        after_patch_response = api_client.get(public_url)
        assert after_patch_response.status_code == 200
        assert after_patch_response.json()["city"] == "Alexandria"
