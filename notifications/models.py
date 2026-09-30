"""
notifications app models (Part P-078).

Two models live here:

* Notification - one in-app notification row for one recipient.
* NotificationPreference - one row per user with a small set of
  category-level on/off toggles.

The in-app half (this module + notifications.services.create_notification)
and the push half (notifications.services.send_push_notification) are
deliberately separate concerns; see notifications/services.py.
"""

from django.conf import settings
from django.db import models

from core.models import TimestampedModel


class Notification(TimestampedModel):
    """
    One in-app notification for one recipient.

    ==========================================================
    notification_type vs deep_link_type + target_id
    ==========================================================
    These are two DIFFERENT questions and must stay two different
    fields. Do not merge them.

    * notification_type answers WHY the notification exists
      ("new_follower", "comment_on_content", "chat_message", ...).
      It drives the icon, the wording, and which
      NotificationPreference category can mute it.

    * deep_link_type + target_id answer WHERE tapping the
      notification navigates ("business_profile" + a BusinessProfile
      id, "chat_thread" + a Conversation id, ...). They drive the
      Flutter router only.

    The mapping between the two is many-to-many in practice: two
    different notification_types (new_follower and new_rating) both
    deep-link to the same "business_profile" screen type, and one
    notification_type (moderation_approved) can deep-link to a
    different screen type depending on what was approved (post_detail
    or reel_detail).

    deep_link_type may be blank (a notification that navigates
    nowhere, e.g. a plain system announcement). target_id is
    nullable for the same reason. The meaning of target_id depends on
    deep_link_type: business_profile -> BusinessProfile id,
    post_detail -> Post id, reel_detail -> Reel id, product_detail ->
    Product id, chat_thread -> Conversation id.

    Notification inherits TimestampedModel only (no SoftDeleteModel):
    it is per-user bookkeeping, not user-facing content, so it is
    never moderated or soft-hidden.
    """

    class NotificationType(models.TextChoices):
        CHAT_MESSAGE = "chat_message", "Chat message"
        MODERATION_APPROVED = "moderation_approved", "Moderation approved"
        MODERATION_REJECTED = "moderation_rejected", "Moderation rejected"
        NEW_FOLLOWER = "new_follower", "New follower"
        COMMENT_ON_CONTENT = "comment_on_content", "Comment on content"
        NEW_LIKE = "new_like", "New like"
        NEW_SHARE = "new_share", "New share"
        NEW_RATING = "new_rating", "New rating"
        SYSTEM_ANNOUNCEMENT = "system_announcement", "System announcement"

    class DeepLinkType(models.TextChoices):
        BUSINESS_PROFILE = "business_profile", "Business profile"
        POST_DETAIL = "post_detail", "Post detail"
        REEL_DETAIL = "reel_detail", "Reel detail"
        PRODUCT_DETAIL = "product_detail", "Product detail"
        CHAT_THREAD = "chat_thread", "Chat thread"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    notification_type = models.CharField(
        max_length=32,
        choices=NotificationType.choices,
    )
    title = models.CharField(max_length=255)
    body = models.TextField()
    deep_link_type = models.CharField(
        max_length=32,
        choices=DeepLinkType.choices,
        blank=True,
        default="",
    )
    target_id = models.PositiveIntegerField(null=True, blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            # Supports the notification-center list: one user's
            # notifications, newest first.
            models.Index(
                fields=["recipient", "-created_at"],
                name="notif_recipient_created_idx",
            ),
            # Supports the unread-badge count for one user.
            models.Index(
                fields=["recipient", "is_read"],
                name="notif_recipient_read_idx",
            ),
        ]

    def __str__(self):
        return (
            f"notification:{self.pk} {self.notification_type} "
            f"-> user:{self.recipient_id}"
        )


class NotificationPreference(TimestampedModel):
    """
    Per-user, category-level notification toggles (one row per user).

    Deliberately a small set of categories, NOT one flag per
    Notification.NotificationType - a per-type flag list would be far
    too granular for a usable settings screen.

    Category -> notification_type mapping (consumed by the dispatch
    task in Part P-079, not enforced here):

    * chat_notifications_enabled -> chat_message
    * moderation_notifications_enabled -> moderation_approved,
      moderation_rejected
    * social_notifications_enabled -> new_follower, comment_on_content,
      new_like, new_share, new_rating

    system_announcement has no toggle: it is always delivered.

    Rows are created automatically by the post_save signal in
    notifications/signals.py (all toggles default True), so no
    user-creation code path has to remember to create one.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    chat_notifications_enabled = models.BooleanField(default=True)
    moderation_notifications_enabled = models.BooleanField(default=True)
    social_notifications_enabled = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Notification preference"
        verbose_name_plural = "Notification preferences"

    def __str__(self):
        return f"notification preferences for user:{self.user_id}"
