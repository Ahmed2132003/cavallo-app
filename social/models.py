from django.conf import settings
from django.db import models

from core.models import SoftDeleteModel, TimestampedModel


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


from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType


class Like(TimestampedModel):
    """
    A generic like relationship: a User liking any Moderatable content
    object that exposes a `likes_count` field (Post, Reel — see
    social/views.py's ALLOWED_CONTENT_TYPES for the exact whitelist).

    Second real application of P-052's idempotent-toggle-plus-atomic-
    counter pattern, this time via a GenericForeignKey rather than a
    fixed-model FK, since Like applies across multiple content types.

    Stories are explicitly NOT likeable — the architecture's Story
    scope (P-046/P-049) is view-tracking only, with no other social
    interaction. If that changes, add "story" to ALLOWED_CONTENT_TYPES
    explicitly rather than opening this up generically.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="likes",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    class Meta:
        unique_together = ("user", "content_type", "object_id")

    def __str__(self):
        return f"user:{self.user_id} -> {self.content_type_id}:{self.object_id}"


class Save(TimestampedModel):
    """
    A private bookmark: a User saving a Post, Reel or Product for
    later reference. Unlike Like/Follow, there is NO public counter
    anywhere for Save — the architecture's product-discovery
    convention treats saves as visible only to the saving user via
    their own list endpoint (see social/views.py's SaveListView,
    Part P-054).

    Product is included alongside Post/Reel (unlike Like, which is
    Post/Reel-only per P-053) — a customer bookmarking a product for
    later is a natural MVP use case, per this part's own spec
    interpretation. See social/views.py's SAVE_ALLOWED_CONTENT_TYPES
    for the exact whitelist.

    Mirrors Like's exact generic-FK shape (P-053) since Save applies
    across multiple content types too.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saves",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    class Meta:
        unique_together = ("user", "content_type", "object_id")

    def __str__(self):
        return f"user:{self.user_id} -> save:{self.content_type_id}:{self.object_id}"


class Comment(TimestampedModel, SoftDeleteModel):
    """
    A user-submitted text comment on a Post or Reel (Part P-055).

    ==========================================================
    DELIBERATE, CONFIRMED ARCHITECTURAL EXCEPTION — NOT AN OVERSIGHT
    ==========================================================
    Comment deliberately does NOT inherit `moderation.models.Moderatable`.
    Every other content type (Post, Reel, Story) is pre-publish moderated;
    Comment is the one content type that publishes IMMEDIATELY, with no
    `status` field, no `pending_review` state, and no ModerationQueue
    row ever created for it (the moderation post_save signal only fires
    for Moderatable subclasses, so simply not inheriting it is what
    keeps Comment out of the queue). Moderation is REACTIVE only: users
    report a comment (Part P-057), which increments `reports_count`, and
    once that reaches COMMENT_AUTO_HIDE_THRESHOLD
    (social/services.py) the comment is auto-hidden via `is_hidden`.

    Do NOT add Moderatable, a status field, or any "light review" step
    here. If that ever needs to change, it is a product decision that
    must be confirmed explicitly, not a bug to fix.

    `reports_count` is incremented by Part P-057 (Report), not by this
    part; P-057 must then call
    `social.services.check_and_hide_if_threshold_exceeded(comment)`
    rather than re-implementing the threshold logic.

    Unlike Like/Save there is NO unique_together: a user may comment
    on the same object any number of times.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")
    text = models.TextField()
    reports_count = models.PositiveIntegerField(default=0)
    is_hidden = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(
                fields=["content_type", "object_id"],
                name="social_comment_target_idx",
            ),
        ]

    def __str__(self):
        return (
            f"comment:{self.pk} by user:{self.user_id} "
            f"on {self.content_type_id}:{self.object_id}"
        )
