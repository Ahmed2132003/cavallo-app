"""
Tests for analytics.tasks.compute_daily_stats (Part P-084).

Runs the task as a plain function call (same convention as
stories/tests/test_tasks.py). created_at is auto_now_add, so activity is
backdated with QuerySet.update() through _base_manager (test setup only).
"""

import itertools
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest import mock

import pytest
from celery.schedules import crontab
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from analytics import tasks as analytics_tasks
from analytics.models import BusinessDailyStats
from analytics.tasks import _default_target_date, compute_daily_stats
from businesses.models import BusinessProfile
from businesses.services import create_business_profile
from categories.models import Category
from content.models import Post, Reel
from products.models import Product
from ratings.models import Rating
from social.models import Comment, Follow, Like
from stories.models import Story, StoryView

User = get_user_model()

DAY = timezone.localdate() - timedelta(days=7)
_seq = itertools.count(1)


def _at(day, hour=12, minute=0, second=0):
    return datetime.combine(day, time(hour, minute, second), tzinfo=dt_timezone.utc)


def _make_user(account_type="customer"):
    n = next(_seq)
    email = f"analytics-user-{n}@example.com"
    return User.objects.create_user(
        username=email,
        email=email,
        password="testpass123",
        account_type=account_type,
    )


def _make_business(name="Analytics Test Trader"):
    return create_business_profile(
        user=_make_user("business"),
        business_name=name,
        business_type="trader",
        country="EG",
        city="Ismailia",
    )


def _make_post(business):
    return Post.objects.create(business=business, caption="hi")


def _make_reel(business):
    return Reel.objects.create(
        business=business,
        caption="hi",
        video=SimpleUploadedFile(
            "raw.mp4",
            b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom",
            content_type="video/mp4",
        ),
    )


def _make_story(business):
    return Story.objects.create(
        business=business,
        media=SimpleUploadedFile(
            "story.jpg", b"not-a-real-image-just-bytes", content_type="image/jpeg"
        ),
    )


def _backdate(obj, when):
    type(obj)._base_manager.filter(pk=obj.pk).update(created_at=when)


def _follow(business, when):
    obj = Follow.objects.create(follower=_make_user(), business=business)
    _backdate(obj, when)
    return obj


def _like(target, when):
    obj = Like.objects.create(
        user=_make_user(),
        content_type=ContentType.objects.get_for_model(type(target)),
        object_id=target.pk,
    )
    _backdate(obj, when)
    return obj


def _comment(target, when):
    obj = Comment.objects.create(
        user=_make_user(),
        content_type=ContentType.objects.get_for_model(type(target)),
        object_id=target.pk,
        text="nice",
    )
    _backdate(obj, when)
    return obj


def _story_view(story, when):
    obj = StoryView.objects.create(story=story, viewer=_make_user())
    _backdate(obj, when)
    return obj


def _counts(row):
    return (
        row.new_followers,
        row.total_likes_received,
        row.total_comments_received,
        row.total_story_views,
    )


@pytest.mark.django_db
class TestAggregation:
    def test_exact_counts_for_known_fixture(self):
        # 3 follows, 5 likes (3 post + 2 reel), 2 comments, 1 story view.
        business = _make_business()
        post, reel, story = (
            _make_post(business),
            _make_reel(business),
            _make_story(business),
        )
        noon = _at(DAY)
        for _ in range(3):
            _follow(business, noon)
        for _ in range(3):
            _like(post, noon)
        for _ in range(2):
            _like(reel, noon)
        _comment(post, noon)
        _comment(reel, noon)
        _story_view(story, noon)

        compute_daily_stats(target_date=DAY)

        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert _counts(row) == (3, 5, 2, 1)

    def test_day_boundaries_are_exact(self):
        business = _make_business()
        post, story = _make_post(business), _make_story(business)
        before = _at(DAY - timedelta(days=1), 23, 59, 59)  # excluded
        start = _at(DAY, 0, 0, 0)  # included
        end = _at(DAY, 23, 59, 59)  # included
        after = _at(DAY + timedelta(days=1), 0, 0, 0)  # excluded
        for when in (before, start, end, after):
            _follow(business, when)
            _like(post, when)
            _comment(post, when)
            _story_view(story, when)

        compute_daily_stats(target_date=DAY)

        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert _counts(row) == (2, 2, 2, 2)

    def test_other_business_activity_is_not_mixed_in(self):
        a, b = _make_business("A"), _make_business("B")
        post_b, story_b = _make_post(b), _make_story(b)
        noon = _at(DAY)
        for _ in range(2):
            _follow(a, noon)
        for _ in range(4):
            _follow(b, noon)
        _like(post_b, noon)
        _comment(post_b, noon)
        _story_view(story_b, noon)

        compute_daily_stats(target_date=DAY)

        assert _counts(BusinessDailyStats.objects.get(business=a, date=DAY)) == (
            2,
            0,
            0,
            0,
        )
        assert _counts(BusinessDailyStats.objects.get(business=b, date=DAY)) == (
            4,
            1,
            1,
            1,
        )

    def test_soft_deleted_comment_is_not_counted(self):
        business = _make_business()
        post = _make_post(business)
        noon = _at(DAY)
        _comment(post, noon)
        removed = _comment(post, noon)
        removed.delete()  # soft delete

        compute_daily_stats(target_date=DAY)

        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert row.total_comments_received == 1

    def test_activity_on_soft_deleted_post_is_still_counted(self):
        business = _make_business()
        post = _make_post(business)
        _like(post, _at(DAY))
        post.delete()  # soft delete

        compute_daily_stats(target_date=DAY)

        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert row.total_likes_received == 1

    def test_business_without_activity_gets_a_zero_row(self):
        business = _make_business()

        compute_daily_stats(target_date=DAY)

        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert _counts(row) == (0, 0, 0, 0)

    def test_different_dates_produce_different_rows(self):
        business = _make_business()
        _follow(business, _at(DAY))

        compute_daily_stats(target_date=DAY)
        compute_daily_stats(target_date=DAY - timedelta(days=1))

        assert BusinessDailyStats.objects.filter(business=business).count() == 2


@pytest.mark.django_db
class TestIdempotency:
    def test_rerun_keeps_one_row_with_unchanged_values(self):
        business = _make_business()
        post = _make_post(business)
        for _ in range(3):
            _follow(business, _at(DAY))
        for _ in range(5):
            _like(post, _at(DAY))

        compute_daily_stats(target_date=DAY)
        compute_daily_stats(target_date=DAY)

        rows = BusinessDailyStats.objects.filter(business=business, date=DAY)
        assert rows.count() == 1
        assert _counts(rows.get()) == (3, 5, 0, 0)

    def test_rerun_refreshes_the_row_when_new_activity_appears(self):
        business = _make_business()
        _follow(business, _at(DAY))
        compute_daily_stats(target_date=DAY)

        _follow(business, _at(DAY, 18))
        compute_daily_stats(target_date=DAY)

        rows = BusinessDailyStats.objects.filter(business=business, date=DAY)
        assert rows.count() == 1
        assert rows.get().new_followers == 2


@pytest.mark.django_db
class TestTargetDate:
    def test_default_helper_is_yesterday(self):
        assert _default_target_date() == timezone.localdate() - timedelta(days=1)

    def test_default_target_date_is_used_when_none_is_passed(self):
        business = _make_business()
        _follow(business, _at(DAY))

        with mock.patch("analytics.tasks._default_target_date", return_value=DAY):
            result = compute_daily_stats()

        assert result["date"] == DAY.isoformat()
        assert BusinessDailyStats.objects.get(business=business, date=DAY)

    def test_accepts_iso_string_date_like_celery_json(self):
        business = _make_business()
        _follow(business, _at(DAY))

        compute_daily_stats(target_date=DAY.isoformat())

        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert row.new_followers == 1

    def test_future_date_is_rejected(self):
        with pytest.raises(ValueError):
            compute_daily_stats(target_date=timezone.localdate() + timedelta(days=1))


@pytest.mark.django_db
class TestResilience:
    def test_one_failing_business_does_not_stop_the_others(self):
        good, broken = _make_business("Good"), _make_business("Broken")
        real = analytics_tasks._compute_metrics

        def flaky(business, *args, **kwargs):
            if business.pk == broken.pk:
                raise RuntimeError("boom")
            return real(business, *args, **kwargs)

        with mock.patch.object(analytics_tasks, "_compute_metrics", side_effect=flaky):
            result = compute_daily_stats(target_date=DAY)

        assert result["businesses_processed"] == 1
        assert result["businesses_failed"] == 1
        assert BusinessDailyStats.objects.filter(business=good, date=DAY).exists()
        assert not BusinessDailyStats.objects.filter(
            business=broken, date=DAY
        ).exists()


class TestBeatRegistration:
    def test_task_name(self):
        assert compute_daily_stats.name == "analytics.compute_daily_stats"

    def test_daily_rollup_is_registered_at_0015_utc(self):
        entry = settings.CELERY_BEAT_SCHEDULE["compute-business-daily-stats"]
        assert entry["task"] == "analytics.compute_daily_stats"
        assert entry["schedule"] == crontab(hour=0, minute=15)


# ---------------------------------------------------------------------------
# Part P-093: rating + catalog-growth metrics
# ---------------------------------------------------------------------------


def _rate(business, when, score=5):
    obj = Rating.objects.create(customer=_make_user(), business=business, score=score)
    _backdate(obj, when)
    return obj


def _make_published_post(business):
    return Post.objects.create(business=business, caption="pub", status="published")


def _make_published_reel(business):
    reel = _make_reel(business)
    type(reel)._base_manager.filter(pk=reel.pk).update(
        status="published", processing_status="ready"
    )
    return reel


def _make_product(business, is_active=True):
    n = next(_seq)
    return Product.objects.create(
        business=business,
        category=Category.objects.create(name=f"P093 Category {n}"),
        name=f"P093 Product {n}",
        description="desc",
        price="10.00",
        currency=Product.CURRENCY_EGP,
        is_active=is_active,
    )


def _set_average_rating(business, value):
    BusinessProfile.objects.filter(pk=business.pk).update(average_rating=value)


def _p093(row):
    return (
        row.new_ratings_count,
        row.average_rating_snapshot,
        row.active_products_count,
        row.published_posts_count,
        row.published_reels_count,
    )


@pytest.mark.django_db
class TestRatingAndCatalogMetrics:
    def test_exact_values_for_known_fixture(self):
        # 3 ratings, 5 published posts, 2 published reels, 4 active products,
        # average rating 4.33.
        business = _make_business()
        noon = _at(DAY)
        for _ in range(3):
            _rate(business, noon)
        for _ in range(5):
            _make_published_post(business)
        for _ in range(2):
            _make_published_reel(business)
        for _ in range(4):
            _make_product(business)
        _set_average_rating(business, Decimal("4.33"))

        compute_daily_stats(target_date=DAY)

        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert _p093(row) == (3, Decimal("4.33"), 4, 5, 2)

    def test_new_ratings_day_boundaries_are_exact(self):
        business = _make_business()
        before = _at(DAY - timedelta(days=1), 23, 59, 59)  # excluded
        start = _at(DAY, 0, 0, 0)  # included
        end = _at(DAY, 23, 59, 59)  # included
        after = _at(DAY + timedelta(days=1), 0, 0, 0)  # excluded
        for when in (before, start, end, after):
            _rate(business, when)

        compute_daily_stats(target_date=DAY)

        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert row.new_ratings_count == 2

    def test_other_business_data_is_not_mixed_in(self):
        a, b = _make_business("A"), _make_business("B")
        noon = _at(DAY)
        _rate(a, noon)
        for _ in range(3):
            _rate(b, noon)
        _make_published_post(a)
        for _ in range(2):
            _make_published_post(b)
        _make_published_reel(b)
        _make_product(a)
        for _ in range(2):
            _make_product(b)
        _set_average_rating(a, Decimal("1.50"))
        _set_average_rating(b, Decimal("4.75"))

        compute_daily_stats(target_date=DAY)

        row_a = BusinessDailyStats.objects.get(business=a, date=DAY)
        row_b = BusinessDailyStats.objects.get(business=b, date=DAY)
        assert _p093(row_a) == (1, Decimal("1.50"), 1, 1, 0)
        assert _p093(row_b) == (3, Decimal("4.75"), 2, 2, 1)

    def test_only_published_active_and_not_deleted_catalog_is_counted(self):
        business = _make_business()
        _make_published_post(business)
        _make_post(business)  # pending_review: not counted
        gone = _make_published_post(business)
        gone.delete()  # soft delete: not counted
        _make_published_reel(business)
        _make_reel(business)  # pending_review: not counted
        _make_product(business)
        _make_product(business, is_active=False)  # hidden: not counted
        removed = _make_product(business)
        removed.delete()  # soft delete: not counted

        compute_daily_stats(target_date=DAY)

        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert row.published_posts_count == 1
        assert row.published_reels_count == 1
        assert row.active_products_count == 1

    def test_average_rating_snapshot_is_stored_not_live(self):
        business = _make_business()
        _set_average_rating(business, Decimal("4.00"))
        compute_daily_stats(target_date=DAY)

        # The live value changes afterwards; the stored row must not follow.
        _set_average_rating(business, Decimal("2.00"))
        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert row.average_rating_snapshot == Decimal("4.00")

        # Only a re-run of the task refreshes the snapshot.
        compute_daily_stats(target_date=DAY)
        row.refresh_from_db()
        assert row.average_rating_snapshot == Decimal("2.00")

    def test_unrated_empty_business_gets_zero_values(self):
        business = _make_business()

        compute_daily_stats(target_date=DAY)

        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert _p093(row) == (0, Decimal("0.00"), 0, 0, 0)


@pytest.mark.django_db
class TestRatingAndCatalogIdempotency:
    def test_rerun_keeps_one_row_with_unchanged_p093_values(self):
        business = _make_business()
        for _ in range(3):
            _rate(business, _at(DAY))
        for _ in range(5):
            _make_published_post(business)
        _set_average_rating(business, Decimal("4.20"))

        compute_daily_stats(target_date=DAY)
        compute_daily_stats(target_date=DAY)

        rows = BusinessDailyStats.objects.filter(business=business, date=DAY)
        assert rows.count() == 1
        assert _p093(rows.get()) == (3, Decimal("4.20"), 0, 5, 0)

    def test_rerun_updates_the_row_including_new_fields(self):
        business = _make_business()
        _rate(business, _at(DAY))
        _make_published_post(business)
        compute_daily_stats(target_date=DAY)

        _rate(business, _at(DAY, 18))
        _make_published_post(business)
        _make_product(business)
        compute_daily_stats(target_date=DAY)

        rows = BusinessDailyStats.objects.filter(business=business, date=DAY)
        assert rows.count() == 1
        row = rows.get()
        assert row.new_ratings_count == 2
        assert row.published_posts_count == 2
        assert row.active_products_count == 1