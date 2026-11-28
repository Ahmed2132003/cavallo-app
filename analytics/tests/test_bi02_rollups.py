"""BI-02 rollups: product/content daily stats and the compute_daily_stats seam."""

import pytest
from django.contrib.contenttypes.models import ContentType
from content.models import Post
from analytics import rollups
from analytics import tasks as analytics_tasks
from analytics.models import (
    BusinessDailyStats,
    ContentDailyStats,
    ProductDailyStats,
    ProductViewEvent,
)
from analytics.tasks import compute_daily_stats
from analytics.tests.bi02_helpers import (
    DAY1,
    DAY2,
    at,
    backdate,
    make_business,
    make_content_event,
    make_customer,
    make_interest,
    make_post,
    make_product,
    make_product_event,
    make_reel,
)
from businesses.models import BusinessProfile
from products.models import Product
from social.models import Comment, Like, Save, Share

pytestmark = pytest.mark.django_db


def _ct(model):
    return ContentType.objects.get_for_model(model)


def _row(product, day=DAY1):
    return ProductDailyStats.objects.get(product=product, date=day)


# --------------------------------------------------------------- products
def test_views_and_unique_views_per_day():
    product = make_product(make_business())
    make_product_event(product, "viewer-a", at(DAY1, 9))
    make_product_event(product, "viewer-a", at(DAY1, 15))
    make_product_event(product, "viewer-b", at(DAY1, 18))
    make_product_event(product, "viewer-a", at(DAY2, 9))

    assert rollups.rollup_product_stats(DAY1) == 1
    row = _row(product)
    assert (row.views, row.unique_views, row.interests, row.saves) == (3, 2, 0, 0)
    assert row.business_id == product.business_id

    rollups.rollup_product_stats(DAY2)
    day2 = _row(product, DAY2)
    assert (day2.views, day2.unique_views) == (1, 1)


def test_unique_view_is_per_viewer_per_day_not_global():
    product = make_product(make_business())
    for day in (DAY1, DAY2):
        make_product_event(product, "same-viewer", at(day))
    rollups.rollup_product_stats(DAY1)
    rollups.rollup_product_stats(DAY2)
    assert _row(product, DAY1).unique_views == 1
    assert _row(product, DAY2).unique_views == 1


def test_interests_count_distinct_customers_opened_that_day():
    business = make_business()
    product = make_product(business)
    make_interest(business, product, make_customer(), at(DAY1))
    make_interest(business, product, make_customer(), at(DAY1, 20))
    make_interest(business, product, make_customer(), at(DAY2))

    rollups.rollup_product_stats(DAY1)
    row = _row(product)
    assert (row.views, row.interests) == (0, 2)


def test_product_saves_are_counted_from_social_save():
    product = make_product(make_business())
    save = Save.objects.create(
        user=make_customer(), content_type=_ct(Product), object_id=product.pk
    )
    backdate(save, at(DAY1))
    rollups.rollup_product_stats(DAY1)
    assert _row(product).saves == 1


def test_product_rollup_is_idempotent():
    product = make_product(make_business())
    make_product_event(product, "a", at(DAY1))
    rollups.rollup_product_stats(DAY1)
    first = ProductDailyStats.objects.values_list("pk", "views", "unique_views")
    first = list(first)
    rollups.rollup_product_stats(DAY1)
    assert (
        list(ProductDailyStats.objects.values_list("pk", "views", "unique_views"))
        == first
    )
    assert ProductDailyStats.objects.count() == 1


def test_recomputing_a_past_day_picks_up_new_events():
    product = make_product(make_business())
    make_product_event(product, "a", at(DAY1))
    rollups.rollup_product_stats(DAY1)
    make_product_event(product, "b", at(DAY1, 20))
    rollups.rollup_product_stats(DAY1)
    row = _row(product)
    assert (row.views, row.unique_views) == (2, 2)


def test_stale_rows_without_activity_are_deleted_only_for_that_day():
    product = make_product(make_business())
    make_product_event(product, "a", at(DAY1))
    make_product_event(product, "a", at(DAY2))
    rollups.rollup_product_stats(DAY1)
    rollups.rollup_product_stats(DAY2)

    ProductViewEvent.objects.filter(view_date=DAY1).delete()
    rollups.rollup_product_stats(DAY1)

    assert not ProductDailyStats.objects.filter(date=DAY1).exists()
    assert ProductDailyStats.objects.filter(date=DAY2).exists()


# ---------------------------------------------------------------- content
def test_content_views_and_social_counts():
    business = make_business()
    post = make_post(business)
    make_content_event("post", post, "v1", at(DAY1))
    make_content_event("post", post, "v1", at(DAY1, 14))
    make_content_event("post", post, "v2", at(DAY1, 16))

    like = Like.objects.create(
        user=make_customer(), content_type=_ct(Post), object_id=post.pk
    )
    backdate(like, at(DAY1))
    for _ in range(2):
        c = Comment.objects.create(
            user=make_customer(),
            content_type=_ct(Post),
            object_id=post.pk,
            text="hi",
        )
        backdate(c, at(DAY1))
    removed = Comment.objects.create(
        user=make_customer(), content_type=_ct(Post), object_id=post.pk, text="x"
    )
    backdate(removed, at(DAY1))
    removed.delete()  # soft delete: must not be counted
    save = Save.objects.create(
        user=make_customer(), content_type=_ct(Post), object_id=post.pk
    )
    backdate(save, at(DAY1))
    share = Share.objects.create(
        user=make_customer(), content_type=_ct(Post), object_id=post.pk
    )
    backdate(share, at(DAY1))
    outside = Like.objects.create(
        user=make_customer(), content_type=_ct(Post), object_id=post.pk
    )
    backdate(outside, at(DAY2))

    rollups.rollup_content_stats(DAY1)
    row = ContentDailyStats.objects.get(
        content_kind="post", object_id=post.pk, date=DAY1
    )
    assert (
        row.views,
        row.unique_views,
        row.likes,
        row.comments,
        row.saves,
        row.shares,
    ) == (3, 2, 1, 2, 1, 1)
    assert row.business_id == business.pk


def test_social_only_activity_still_gets_a_row_and_survives_soft_delete():
    post = make_post(make_business())
    like = Like.objects.create(
        user=make_customer(), content_type=_ct(Post), object_id=post.pk
    )
    backdate(like, at(DAY1))
    post.delete()

    rollups.rollup_content_stats(DAY1)
    row = ContentDailyStats.objects.get(object_id=post.pk, date=DAY1)
    assert (row.views, row.likes, row.business_id) == (0, 1, post.business_id)


def test_post_and_reel_with_same_object_id_are_separate_rows():
    business = make_business()
    post = make_post(business)
    reel = make_reel(business)
    for obj, model in ((post, Post), (reel, type(reel))):
        like = Like.objects.create(
            user=make_customer(), content_type=_ct(model), object_id=obj.pk
        )
        backdate(like, at(DAY1))
    rollups.rollup_content_stats(DAY1)
    kinds = set(ContentDailyStats.objects.values_list("content_kind", flat=True))
    assert kinds == {"post", "reel"}


def test_content_rollup_is_idempotent_and_removes_stale_rows():
    post = make_post(make_business())
    like = Like.objects.create(
        user=make_customer(), content_type=_ct(Post), object_id=post.pk
    )
    backdate(like, at(DAY1))
    rollups.rollup_content_stats(DAY1)
    rollups.rollup_content_stats(DAY1)
    assert ContentDailyStats.objects.count() == 1
    Like.objects.all().delete()
    rollups.rollup_content_stats(DAY1)
    assert ContentDailyStats.objects.count() == 0


# ------------------------------------------------------------- run_bi_rollups
def test_run_bi_rollups_marks_only_that_day():
    business = make_business()
    product = make_product(business)
    make_product_event(product, "a", at(DAY1))
    mark_row_1 = BusinessDailyStats.objects.create(business=business, date=DAY1)
    mark_row_2 = BusinessDailyStats.objects.create(business=business, date=DAY2)

    summary = rollups.run_bi_rollups(DAY1)
    assert summary == {"product_rows": 1, "content_rows": 0, "businesses_marked": 1}
    mark_row_1.refresh_from_db()
    mark_row_2.refresh_from_db()
    assert mark_row_1.bi_rollups_computed_at is not None
    assert mark_row_2.bi_rollups_computed_at is None

    first_mark = mark_row_1.bi_rollups_computed_at
    rollups.run_bi_rollups(DAY1)
    mark_row_1.refresh_from_db()
    assert mark_row_1.bi_rollups_computed_at >= first_mark


def test_run_bi_rollups_is_all_or_nothing(monkeypatch):
    business = make_business()
    product = make_product(business)
    make_product_event(product, "a", at(DAY1))
    row = BusinessDailyStats.objects.create(business=business, date=DAY1)

    def boom(_day):
        raise RuntimeError("content rollup failed")

    monkeypatch.setattr(rollups, "rollup_content_stats", boom)
    with pytest.raises(RuntimeError):
        rollups.run_bi_rollups(DAY1)

    assert ProductDailyStats.objects.count() == 0
    row.refresh_from_db()
    assert row.bi_rollups_computed_at is None


# ------------------------------------------------------ compute_daily_stats
def test_compute_daily_stats_runs_bi_rollups_and_keeps_return_shape():
    business = make_business()
    product = make_product(business)
    make_product_event(product, "a", at(DAY1))

    result = compute_daily_stats(target_date=DAY1)

    assert set(result) == {"date", "businesses_processed", "businesses_failed"}
    assert result["date"] == DAY1.isoformat()
    assert result["businesses_processed"] == BusinessProfile.objects.count()
    assert result["businesses_failed"] == 0
    stats = BusinessDailyStats.objects.get(business=business, date=DAY1)
    assert stats.bi_rollups_computed_at is not None
    assert ProductDailyStats.objects.get(product=product, date=DAY1).views == 1


def test_bi_failure_does_not_touch_business_daily_stats(monkeypatch):
    business = make_business()

    def boom(_day):
        raise RuntimeError("bi down")

    monkeypatch.setattr(analytics_tasks, "run_bi_rollups", boom)
    result = compute_daily_stats(target_date=DAY1)

    assert result["businesses_failed"] == 0
    stats = BusinessDailyStats.objects.get(business=business, date=DAY1)
    assert stats.bi_rollups_computed_at is None
