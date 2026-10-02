"""
Daily analytics rollup — Celery Beat job (Part P-084).

Architecture Section 17: basic analytics are a DAILY ROLLUP, never live
aggregation on every dashboard request. For every BusinessProfile this
task aggregates one UTC calendar day of raw activity into a single
BusinessDailyStats row.

Idempotent via update_or_create keyed on (business, date): re-running a
date refreshes the row instead of duplicating it (Architecture Section 5,
rule 8).

What is counted (and what is deliberately NOT):
  - new_followers: social.Follow rows created on the date. Unfollow is a
    real row deletion (P-052), so a follow that was undone before the job
    ran is not counted.
  - total_likes_received: social.Like rows created on the date whose target
    is one of the business's Posts/Reels. Unlike deletes the row (P-053).
    Soft-deleted Posts/Reels are INCLUDED (all_objects) so recomputing an
    old date gives the same number even if content was deleted later.
  - total_comments_received: social.Comment rows (soft-deleted comments are
    excluded by Comment.objects) on the business's Posts/Reels.
  - total_story_views: stories.StoryView rows on the business's Stories.
  - new_ratings_count (P-093): ratings.Rating rows created on the date. A
    customer re-rating a business UPDATES their single row (unique per
    customer + business), so only first-time ratings are counted here,
    not later edits.
  - average_rating_snapshot (P-093): BusinessProfile.average_rating at the
    moment this task reaches that business. It is a stored POINT-IN-TIME
    SNAPSHOT (so a trend chart can show what the rating was on each past
    day), never a live join. Timing nuance: the Beat job runs at 00:15 UTC
    for the previous day, so the value used is the rating as of ~00:15 UTC
    the next day. Re-running an OLD date (backfill) stores TODAY'S average,
    not the historical one: the past cannot be reconstructed.
  - active_products_count / published_posts_count / published_reels_count
    (P-093): catalog totals at task-execution time (snapshots, not
    per-day deltas). Same backfill caveat as the rating snapshot.
  - Product views / profile views are NOT tracked anywhere in this system,
    so they are NOT computed (documented gap, not a bug).
"""

import logging
from datetime import date, timedelta

from celery import shared_task
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils import timezone

from analytics.models import BusinessDailyStats
from businesses.models import BusinessProfile
from content.models import Post, Reel
from products.models import Product
from ratings.models import Rating
from social.models import Comment, Follow, Like
from stories.models import StoryView

logger = logging.getLogger(__name__)


def _default_target_date():
    """Yesterday (UTC, because settings.TIME_ZONE is UTC)."""
    return timezone.localdate() - timedelta(days=1)


def _resolve_target_date(target_date):
    """
    Accepts None (-> yesterday), a ``date`` or an ISO ``YYYY-MM-DD`` string
    (Celery's JSON serializer turns dates into strings). Future dates are
    rejected: there is no activity to roll up yet.
    """
    if target_date is None:
        return _default_target_date()
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)
    if target_date > timezone.localdate():
        raise ValueError(f"Cannot compute daily stats for a future date: {target_date}")
    return target_date


def _compute_metrics(business, target_date, post_ct, reel_ct):
    """
    Return the nine metric values for one business on one date: P-084's
    four engagement counts plus P-093's rating / catalog-growth fields
    (see the module docstring for the snapshot timing nuance).
    """
    own_content = Q(
        content_type=post_ct,
        object_id__in=Post.all_objects.filter(business=business).values("pk"),
    ) | Q(
        content_type=reel_ct,
        object_id__in=Reel.all_objects.filter(business=business).values("pk"),
    )
    return {
        "new_followers": Follow.objects.filter(
            business=business, created_at__date=target_date
        ).count(),
        "total_likes_received": Like.objects.filter(
            own_content, created_at__date=target_date
        ).count(),
        "total_comments_received": Comment.objects.filter(
            own_content, created_at__date=target_date
        ).count(),
        "total_story_views": StoryView.objects.filter(
            story__business=business, created_at__date=target_date
        ).count(),
        "new_ratings_count": Rating.objects.filter(
            business=business, created_at__date=target_date
        ).count(),
        "average_rating_snapshot": business.average_rating,
        "active_products_count": Product.objects.filter(
            business=business, is_active=True
        ).count(),
        "published_posts_count": Post.published_objects.filter(
            business=business
        ).count(),
        "published_reels_count": Reel.published_objects.filter(
            business=business
        ).count(),
    }


@shared_task(name="analytics.compute_daily_stats", ignore_result=True)
def compute_daily_stats(target_date=None):
    """
    Roll up one day of engagement for every BusinessProfile.

    ``target_date`` defaults to yesterday (normal daily Beat run); pass an
    explicit date (or ISO string) for backfill/testing. One business
    failing is logged and skipped so it cannot block the others.

    Returns {"date", "businesses_processed", "businesses_failed"}.
    """
    target_date = _resolve_target_date(target_date)
    post_ct = ContentType.objects.get_for_model(Post)
    reel_ct = ContentType.objects.get_for_model(Reel)

    processed = 0
    failed = 0
    for business in BusinessProfile.objects.order_by("pk"):
        try:
            metrics = _compute_metrics(business, target_date, post_ct, reel_ct)
            BusinessDailyStats.objects.update_or_create(
                business=business, date=target_date, defaults=metrics
            )
            processed += 1
        except Exception:
            failed += 1
            logger.exception(
                "compute_daily_stats failed for business_id=%s date=%s",
                business.pk,
                target_date,
            )

    return {
        "date": target_date.isoformat(),
        "businesses_processed": processed,
        "businesses_failed": failed,
    }