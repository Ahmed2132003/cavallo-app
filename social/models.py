from django.conf import settings
from django.db import models

from core.models import TimestampedModel


class Follow(TimestampedModel):
    """
    A follower relationship: a User (any account_type) following a
    BusinessProfile. No soft-delete on purpose — unfollow is a real
    row deletion, not a moderation-style hide (mirrors StoryView's
    "pure relationship bookkeeping" precedent from P-049).

    Assumption (flagged per this part's own spec instruction): the
    architecture is silent on whether Business-type accounts may
    follow other businesses, so this allows ANY authenticated user
    (customer or business) to follow a BusinessProfile. Revisit if
    Ahmed wants this restricted.
    """

    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="following",
    )
    business = models.ForeignKey(
        "businesses.BusinessProfile",
        on_delete=models.CASCADE,
        related_name="followers",
    )

    class Meta:
        unique_together = ("follower", "business")

    def __str__(self):
        return f"user:{self.follower_id} -> business:{self.business_id}"
