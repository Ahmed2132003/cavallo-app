"""
API tests for Part P-109 - RateBusinessView / RatingsListView.

Mirrors social/tests/test_api.py's conventions (force_authenticate,
APIClient, pytest.mark.django_db, uuid4-suffixed unique emails).
"""

from uuid import uuid4

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from businesses.models import BusinessProfile
from ratings.models import Rating

pytestmark = pytest.mark.django_db


def _make_user(account_type: str, email: str) -> User:
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type=account_type,
    )


def _make_business(email_suffix: str) -> BusinessProfile:
    owner = _make_user("business", f"p109-api-owner-{email_suffix}@example.com")
    return BusinessProfile.objects.create(
        user=owner,
        business_name="Acme Trading",
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )


@pytest.fixture
def api_client():
    return APIClient()


def _rate_url(pk):
    return reverse("ratings:business-rate", kwargs={"pk": pk})


def _ratings_list_url(pk):
    return reverse("ratings:business-ratings", kwargs={"pk": pk})


class TestRateBusinessView:
    def test_requires_authentication(self, api_client):
        business = _make_business(uuid4().hex[:12])

        response = api_client.post(_rate_url(business.pk), {"score": 4})

        assert response.status_code == 401

    def test_first_rating_returns_recomputed_aggregate(self, api_client):
        customer = _make_user("customer", f"p109-r1-{uuid4().hex[:12]}@example.com")
        business = _make_business(uuid4().hex[:12])
        api_client.force_authenticate(user=customer)

        response = api_client.post(
            _rate_url(business.pk), {"score": 4, "review_text": "Solid."}
        )

        assert response.status_code == 200
        assert response.json() == {"average_rating": "4.00", "ratings_count": 1}
        assert Rating.objects.filter(customer=customer, business=business).count() == 1

    def test_rating_again_upserts_not_duplicates(self, api_client):
        customer = _make_user("customer", f"p109-r2-{uuid4().hex[:12]}@example.com")
        business = _make_business(uuid4().hex[:12])
        api_client.force_authenticate(user=customer)

        api_client.post(_rate_url(business.pk), {"score": 4})
        response = api_client.post(_rate_url(business.pk), {"score": 2})

        assert response.status_code == 200
        assert response.json() == {"average_rating": "2.00", "ratings_count": 1}
        assert Rating.objects.filter(customer=customer, business=business).count() == 1

    @pytest.mark.parametrize("bad_score", [0, 6, -1])
    def test_score_out_of_range_is_rejected(self, api_client, bad_score):
        customer = _make_user("customer", f"p109-r3-{uuid4().hex[:12]}@example.com")
        business = _make_business(uuid4().hex[:12])
        api_client.force_authenticate(user=customer)

        response = api_client.post(_rate_url(business.pk), {"score": bad_score})

        assert response.status_code == 400
        assert Rating.objects.filter(customer=customer, business=business).count() == 0

    def test_unknown_business_returns_404(self, api_client):
        customer = _make_user("customer", f"p109-r4-{uuid4().hex[:12]}@example.com")
        api_client.force_authenticate(user=customer)

        response = api_client.post(_rate_url(999999), {"score": 4})

        assert response.status_code == 404


class TestRatingsListView:
    def test_public_no_auth_required(self, api_client):
        business = _make_business(uuid4().hex[:12])

        response = api_client.get(_ratings_list_url(business.pk))

        assert response.status_code == 200

    def test_lists_only_this_business_ratings(self, api_client):
        business_a = _make_business(uuid4().hex[:12])
        business_b = _make_business(uuid4().hex[:12])
        c1 = _make_user("customer", f"p109-l1-{uuid4().hex[:12]}@example.com")
        c2 = _make_user("customer", f"p109-l2-{uuid4().hex[:12]}@example.com")

        api_client.force_authenticate(user=c1)
        api_client.post(_rate_url(business_a.pk), {"score": 5, "review_text": "A"})
        api_client.force_authenticate(user=c2)
        api_client.post(_rate_url(business_b.pk), {"score": 1, "review_text": "B"})

        response = api_client.get(_ratings_list_url(business_a.pk))

        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 1
        assert results[0]["review_text"] == "A"
        assert results[0]["score"] == 5
