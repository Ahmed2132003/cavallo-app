"""
Part P-092 (STEP 3) - Featured status on /api/v1/businesses/me/.

The owner-only /me/ responses expose is_featured and featured_until
(expires_at of the active FeaturedSubscription). The public profile view
must NOT expose either.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from businesses.models import BusinessProfile
from businesses.services import create_business_profile
from monetization.models import FeaturedSubscription, Plan
from monetization.services import activate_subscription, deactivate_subscriptions

pytestmark = pytest.mark.django_db

ME_URL = reverse("businesses:business-me")


def _make_user(email):
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type="business",
    )


def _make_profile(email="p092-me@example.com", name="Acme"):
    user = _make_user(email)
    profile = create_business_profile(
        user=user,
        business_name=name,
        business_type="trader",
        country="EG",
        city="Cairo",
    )
    return user, profile


def _make_plan(days=30):
    return Plan.objects.create(
        name=f"Featured {days}",
        duration_days=days,
        price=Decimal("250.00"),
        currency="EGP",
    )


def _client_for(user):
    # Fresh User instance, like a real request (the JWT auth loads the user
    # from the DB). The instance used for setup caches business_profile,
    # which activate_subscription() mutates in memory, so reusing it would
    # show stale state that cannot happen in production.
    client = APIClient()
    client.force_authenticate(user=User.objects.get(pk=user.pk))
    return client


def _parse(value):
    return datetime.fromisoformat(value)


def test_new_business_is_not_featured_and_has_no_expiry():
    user, _ = _make_profile()

    body = _client_for(user).get(ME_URL).json()

    assert body["is_featured"] is False
    assert body["featured_until"] is None


def test_active_subscription_is_reported_with_its_expiry():
    user, profile = _make_profile()
    activate_subscription(profile, _make_plan(30))
    sub = FeaturedSubscription.objects.get(business=profile, is_active=True)

    body = _client_for(user).get(ME_URL).json()

    assert body["is_featured"] is True
    assert _parse(body["featured_until"]) == sub.expires_at


def test_after_renewal_the_new_expiry_is_reported():
    user, profile = _make_profile()
    activate_subscription(profile, _make_plan(7))
    activate_subscription(profile, _make_plan(90))
    current = FeaturedSubscription.objects.get(business=profile, is_active=True)

    body = _client_for(user).get(ME_URL).json()

    assert FeaturedSubscription.objects.filter(business=profile).count() == 2
    assert _parse(body["featured_until"]) == current.expires_at


def test_after_deactivation_the_fields_are_cleared():
    user, profile = _make_profile()
    activate_subscription(profile, _make_plan(30))
    deactivate_subscriptions(FeaturedSubscription.objects.filter(business=profile))

    body = _client_for(user).get(ME_URL).json()

    assert body["is_featured"] is False
    assert body["featured_until"] is None


def test_another_businesses_subscription_is_never_reported():
    user, _ = _make_profile("p092-mine@example.com", "Mine")
    _, other = _make_profile("p092-other@example.com", "Other")
    activate_subscription(other, _make_plan(30))

    body = _client_for(user).get(ME_URL).json()

    assert body["is_featured"] is False
    assert body["featured_until"] is None


def test_post_onboarding_response_includes_the_fields():
    user = _make_user("p092-new@example.com")

    response = _client_for(user).post(
        ME_URL,
        {
            "business_name": "Fresh Co",
            "business_type": "factory",
            "country": "EG",
            "city": "Giza",
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["is_featured"] is False
    assert response.json()["featured_until"] is None


def test_patch_response_includes_the_fields_and_ignores_writes_to_them():
    user, profile = _make_profile()

    response = _client_for(user).patch(
        ME_URL,
        {
            "city": "Alexandria",
            "is_featured": True,
            "featured_until": "2099-01-01T00:00:00Z",
        },
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["city"] == "Alexandria"
    assert body["is_featured"] is False
    assert body["featured_until"] is None
    profile.refresh_from_db()
    assert profile.is_featured is False
    assert FeaturedSubscription.objects.count() == 0


def test_public_profile_view_exposes_is_featured_but_not_featured_until():
    # Contract change in P-110: the public profile now carries the boolean
    # is_featured (the Flutter "Featured" badge reads it). The subscription
    # DATE (featured_until) must still never appear on the public, cached
    # read -- that restriction from P-092 is unchanged.
    cache.clear()
    _, profile = _make_profile()
    activate_subscription(profile, _make_plan(30))

    response = APIClient().get(
        reverse("businesses:business-public", kwargs={"pk": profile.pk})
    )

    assert response.status_code == 200
    assert response.json()["is_featured"] is True
    assert "featured_until" not in response.json()


def test_existing_me_fields_are_unchanged():
    user, profile = _make_profile()

    body = _client_for(user).get(ME_URL).json()

    assert body["id"] == profile.pk
    assert body["business_name"] == "Acme"
    assert body["business_type"] == BusinessProfile.BUSINESS_TYPE_TRADER
    assert body["follower_count"] == 0
    assert body["is_verified"] is False
