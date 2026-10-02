from django.db import models

from core.models import TimestampedModel


class BusinessDailyStats(TimestampedModel):
    """
    Part P-084. One row per (business, date): a daily rollup of that
    business's engagement, computed by analytics.tasks.compute_daily_stats
    (Architecture Section 17: daily rollup job, never live aggregation).

    ONLY metrics this system genuinely tracks are stored here:
      - new_followers            <- social.Follow rows created that day
      - total_likes_received     <- social.Like rows on the business's Post/Reel
      - total_comments_received  <- social.Comment rows on the business's Post/Reel
      - total_story_views        <- stories.StoryView rows on the business's Stories

    Part P-093 additions (additive migration 0002), all genuinely backed:
      - new_ratings_count        <- ratings.Rating rows CREATED that day.
                                    A customer re-rating a business updates
                                    their single row (unique per customer +
                                    business), so only first-time ratings
                                    are counted, not later edits.
      - average_rating_snapshot  <- BusinessProfile.average_rating copied at
                                    rollup time: a POINT-IN-TIME SNAPSHOT
                                    for trend lines, NOT a live value.
      - active_products_count /
        published_posts_count /
        published_reels_count    <- snapshots of the catalog totals at
                                    rollup time (catalog-growth chart).

    DOCUMENTED GAP (not a bug): product views and profile views are NOT
    tracked anywhere in this system, so there is deliberately NO field for
    them. Do not add one without first building a real tracking mechanism.

    ``date`` is a UTC calendar date (settings.TIME_ZONE == "UTC").
    """

    business = models.ForeignKey(
        "businesses.BusinessProfile",
        on_delete=models.CASCADE,
        related_name="daily_stats",
    )
    date = models.DateField()
    new_followers = models.PositiveIntegerField(default=0)
    total_likes_received = models.PositiveIntegerField(default=0)
    total_comments_received = models.PositiveIntegerField(default=0)
    total_story_views = models.PositiveIntegerField(default=0)
    # Part P-093 (see class docstring).
    new_ratings_count = models.PositiveIntegerField(default=0)
    # Point-in-time snapshot, same precision as
    # BusinessProfile.average_rating (max_digits=3, decimal_places=2).
    average_rating_snapshot = models.DecimalField(
        max_digits=3, decimal_places=2, default=0
    )
    active_products_count = models.PositiveIntegerField(default=0)
    published_posts_count = models.PositiveIntegerField(default=0)
    published_reels_count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("business", "date")

    def __str__(self):
        return f"BusinessDailyStats(business={self.business_id}, date={self.date})"