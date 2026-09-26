"""
Tests for Part P-109 - ratings.services.rate_business().

Mirrors social/tests/test_api.py's _make_user/_make_business
conventions (pytest.mark.django_db, uuid4-suffixed unique emails).
The upsert/no-double-counting case is the most important test here -
see this part's own Definition of Done.
"""

from uuid import uuid4

import pytest

from accounts.models import User
from businesses.models import BusinessProfile
from ratings.models import Rating
from ratings.services import rate_business

pytestmark = pytest.mark.django_db


def _make_user(account_type: str, email: str) -> User:
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type=account_type,
    )


def _make_business(email_suffix: str) -> BusinessProfile:
    owner = _make_user("business", f"p109-owner-{email_suffix}@example.com")
    return BusinessProfile.objects.create(
        user=owner,
        business_name="Acme Trading",
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )


class TestRateBusinessFirstRating:
    def test_first_rating_creates_row_and_sets_average(self):
        customer = _make_user("customer", f"p109-c1-{uuid4().hex[:12]}@example.com")
        business = _make_business(uuid4().hex[:12])

        rating, created, average, count = rate_business(
            customer=customer, business=business, score=4
        )

        assert created is True
        assert Rating.objects.filter(customer=customer, business=business).count() == 1
        assert rating.score == 4
        assert average == 4
        assert count == 1

        business.refresh_from_db()
        assert business.average_rating == 4
        assert business.ratings_count == 1


class TestRateBusinessUpsert:
    def test_same_customer_rating_again_updates_not_duplicates(self):
        """The critical upsert case (this part's DoD): still exactly ONE
        Rating row for the (customer, business) pair, and the average
        reflects only the NEW score - never a blended/double-counted
        value.
        """
        customer = _make_user("customer", f"p109-c2-{uuid4().hex[:12]}@example.com")
        business = _make_business(uuid4().hex[:12])

        rate_business(customer=customer, business=business, score=4)
        rating, created, average, count = rate_business(
            customer=customer, business=business, score=2
        )

        assert created is False
        assert Rating.objects.filter(customer=customer, business=business).count() == 1
        assert rating.score == 2
        assert average == 2
        assert count == 1

        business.refresh_from_db()
        assert business.average_rating == 2
        assert business.ratings_count == 1

    def test_update_also_replaces_review_text(self):
        customer = _make_user("customer", f"p109-c3-{uuid4().hex[:12]}@example.com")
        business = _make_business(uuid4().hex[:12])

        rate_business(
            customer=customer, business=business, score=3, review_text="Meh."
        )
        rate_business(
            customer=customer,
            business=business,
            score=5,
            review_text="Actually great!",
        )

        rating = Rating.objects.get(customer=customer, business=business)
        assert rating.score == 5
        assert rating.review_text == "Actually great!"


class TestRateBusinessMultiCustomer:
    def test_average_is_mathematically_correct_across_customers(self):
        business = _make_business(uuid4().hex[:12])
        c1 = _make_user("customer", f"p109-m1-{uuid4().hex[:12]}@example.com")
        c2 = _make_user("customer", f"p109-m2-{uuid4().hex[:12]}@example.com")
        c3 = _make_user("customer", f"p109-m3-{uuid4().hex[:12]}@example.com")

        rate_business(customer=c1, business=business, score=5)
        rate_business(customer=c2, business=business, score=3)
        _rating, _created, average, count = rate_business(
            customer=c3, business=business, score=4
        )

        assert count == 3
        assert average == 4  # (5 + 3 + 4) / 3 == 4

        business.refresh_from_db()
        assert business.average_rating == 4
        assert business.ratings_count == 3

    def test_one_customer_updating_does_not_affect_others_correctness(self):
        business = _make_business(uuid4().hex[:12])
        c1 = _make_user("customer", f"p109-m4-{uuid4().hex[:12]}@example.com")
        c2 = _make_user("customer", f"p109-m5-{uuid4().hex[:12]}@example.com")

        rate_business(customer=c1, business=business, score=5)
        rate_business(customer=c2, business=business, score=1)
        # c1 changes their mind.
        rate_business(customer=c1, business=business, score=3)

        business.refresh_from_db()
        assert Rating.objects.filter(business=business).count() == 2
        assert business.average_rating == 2  # (3 + 1) / 2 == 2
        assert business.ratings_count == 2
