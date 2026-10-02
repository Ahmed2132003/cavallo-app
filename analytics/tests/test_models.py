"""Model tests for analytics.BusinessDailyStats (Part P-084)."""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from analytics.models import BusinessDailyStats
from businesses.services import create_business_profile

User = get_user_model()


def _make_business():
    user = User.objects.create_user(
        username="analytics-model-trader@example.com",
        email="analytics-model-trader@example.com",
        password="testpass123",
        account_type="business",
    )
    return create_business_profile(
        user=user,
        business_name="Analytics Model Trader",
        business_type="trader",
        country="EG",
        city="Ismailia",
    )


@pytest.mark.django_db
class TestBusinessDailyStatsModel:
    def test_counters_default_to_zero(self):
        row = BusinessDailyStats.objects.create(
            business=_make_business(), date="2026-01-01"
        )
        assert row.new_followers == 0
        assert row.total_likes_received == 0
        assert row.total_comments_received == 0
        assert row.total_story_views == 0
        assert row.new_ratings_count == 0
        assert row.average_rating_snapshot == 0
        assert row.active_products_count == 0
        assert row.published_posts_count == 0
        assert row.published_reels_count == 0

    def test_unique_together_business_and_date(self):
        business = _make_business()
        BusinessDailyStats.objects.create(business=business, date="2026-01-01")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                BusinessDailyStats.objects.create(business=business, date="2026-01-01")

    def test_p093_fields_store_values(self):
        row = BusinessDailyStats.objects.create(
            business=_make_business(),
            date="2026-01-01",
            new_ratings_count=3,
            average_rating_snapshot=Decimal("4.25"),
            active_products_count=7,
            published_posts_count=5,
            published_reels_count=2,
        )
        row.refresh_from_db()
        assert row.new_ratings_count == 3
        assert row.average_rating_snapshot == Decimal("4.25")
        assert row.active_products_count == 7
        assert row.published_posts_count == 5
        assert row.published_reels_count == 2

    def test_no_fields_for_untracked_metrics(self):
        """
        Product views / profile views are not tracked anywhere in this
        system. This test pins the honest field set so nobody adds a
        fabricated metric by accident.
        """
        names = {f.name for f in BusinessDailyStats._meta.get_fields()}
        assert names == {
            "id",
            "created_at",
            "updated_at",
            "business",
            "date",
            "new_followers",
            "total_likes_received",
            "total_comments_received",
            "total_story_views",
            "new_ratings_count",
            "average_rating_snapshot",
            "active_products_count",
            "published_posts_count",
            "published_reels_count",
        }