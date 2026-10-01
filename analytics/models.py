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

    class Meta:
        unique_together = ("business", "date")

    def __str__(self):
        return f"BusinessDailyStats(business={self.business_id}, date={self.date})"