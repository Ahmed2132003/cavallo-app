"""
Notification dispatch orchestration (Part P-079).

dispatch_notification is the ONE place that turns "something happened
that a user should hear about" into (a) an in-app Notification row and
(b) a push attempt, after checking the recipient's NotificationPreference.

ARCHITECTURE RULE (new required pattern, P-079): no other code may call
notifications.services.create_notification() or
notifications.services.send_push_notification() directly for a real
event. Every source calls dispatch_notification.delay(...). Otherwise
the preference check would have to be re-implemented (and could be
forgotten) per source.

Preference suppression is all-or-nothing: if the recipient disabled the
relevant category, NO Notification row is written and NO push is
attempted. Suppressing only the push while still filling the in-app
notification center would be a confusing half-measure.

----------------------------------------------------------------------
notification_type -> NotificationPreference field (the P-078 contract)
----------------------------------------------------------------------
chat_message          -> chat_notifications_enabled
moderation_approved   -> moderation_notifications_enabled
moderation_rejected   -> moderation_notifications_enabled
new_follower          -> social_notifications_enabled
comment_on_content    -> social_notifications_enabled
new_like              -> social_notifications_enabled
new_share             -> social_notifications_enabled
new_rating            -> social_notifications_enabled
system_announcement   -> (no toggle: always delivered)

----------------------------------------------------------------------
IDEMPOTENCY - honest statement (Section 5 rule 8)
----------------------------------------------------------------------
This task is NOT fully idempotent. "Create a new row" cannot be made
idempotent without a dedup key, and none was built (deliberately: not
over-engineered for the MVP). What IS done instead:

* Call sites enqueue this task exactly once per genuine event.
* The task has no autoretry / retry configuration.
* acks_late is left at Celery's default (False), so a worker crash
  mid-task loses the notification rather than running it twice.
* The push step is best-effort: if it raises, the error is logged and
  swallowed AFTER the in-app row is written, so a push failure never
  causes a Celery retry that would duplicate the row.

Accepted residual risk: a rare Celery double-execution (for example a
broker redelivery) can create one duplicate Notification row and one
duplicate push. That is a low-frequency, low-severity outcome for the
MVP. If it ever matters, add a dedup key (event id) on Notification.
"""

import logging

from celery import shared_task
from django.contrib.auth import get_user_model

from notifications.models import Notification, NotificationPreference
from notifications.services import create_notification, send_push_notification

logger = logging.getLogger(__name__)

User = get_user_model()

# A notification_type that is NOT in this dict has no toggle and is
# always delivered (today only system_announcement).
NOTIFICATION_TYPE_TO_PREFERENCE_FIELD = {
    Notification.NotificationType.CHAT_MESSAGE.value: "chat_notifications_enabled",
    Notification.NotificationType.MODERATION_APPROVED.value: (
        "moderation_notifications_enabled"
    ),
    Notification.NotificationType.MODERATION_REJECTED.value: (
        "moderation_notifications_enabled"
    ),
    Notification.NotificationType.NEW_FOLLOWER.value: "social_notifications_enabled",
    Notification.NotificationType.COMMENT_ON_CONTENT.value: (
        "social_notifications_enabled"
    ),
    Notification.NotificationType.NEW_LIKE.value: "social_notifications_enabled",
    Notification.NotificationType.NEW_SHARE.value: "social_notifications_enabled",
    Notification.NotificationType.NEW_RATING.value: "social_notifications_enabled",
}


def _is_category_enabled(recipient_id, preference_field):
    """
    True unless the recipient explicitly disabled ``preference_field``.

    A missing NotificationPreference row is treated as "all enabled"
    (the model defaults). The post_save signal and the 0002 backfill
    make a missing row unexpected, but a missing row must never turn
    into a crash or a silent block.
    """
    value = (
        NotificationPreference.objects.filter(user_id=recipient_id)
        .values_list(preference_field, flat=True)
        .first()
    )
    return True if value is None else value


@shared_task(name="notifications.dispatch_notification", ignore_result=True)
def dispatch_notification(
    recipient_id,
    notification_type,
    title,
    body,
    deep_link_type=None,
    target_id=None,
):
    recipient = User.objects.filter(pk=recipient_id).first()
    if recipient is None:
        logger.warning(
            "dispatch_notification: recipient user id=%s does not exist; "
            "nothing created, nothing sent (notification_type=%s).",
            recipient_id,
            notification_type,
        )
        return

    preference_field = NOTIFICATION_TYPE_TO_PREFERENCE_FIELD.get(notification_type)
    if preference_field is not None and not _is_category_enabled(
        recipient_id, preference_field
    ):
        logger.info(
            "dispatch_notification: suppressed %s for user %s (%s is disabled).",
            notification_type,
            recipient_id,
            preference_field,
        )
        return

    # Raises ValueError (nothing written, no push) for an unknown
    # notification_type / deep_link_type - a programming error that
    # must be loud, not swallowed.
    notification = create_notification(
        recipient,
        notification_type,
        title,
        body,
        deep_link_type=deep_link_type,
        target_id=target_id,
    )

    try:
        send_push_notification(
            user_id=recipient.id,
            title=title,
            body=body,
            data={
                "type": notification_type,
                "notification_id": notification.id,
                "deep_link_type": notification.deep_link_type,
                "target_id": notification.target_id,
            },
        )
    except Exception:
        # Best-effort: the in-app row already exists. Re-raising would
        # make a retry re-create it (see the idempotency note above).
        logger.exception(
            "dispatch_notification: push failed for notification id=%s "
            "(user %s); the in-app notification was kept.",
            notification.id,
            recipient.id,
        )


def enqueue_notification(**kwargs):
    """
    Best-effort enqueue of dispatch_notification (Part P-079).

    Every notification source (moderation, follow, comment) calls this
    instead of dispatch_notification.delay directly, so a broker outage
    is logged and never turns an already-committed moderation decision /
    follow / comment into an HTTP 500.

    Call it AFTER the surrounding transaction.atomic() block has exited,
    so the worker can never run before the source row is committed.
    """
    try:
        dispatch_notification.delay(**kwargs)
    except Exception:
        logger.exception(
            "enqueue_notification: could not enqueue dispatch_notification "
            "(notification_type=%s, recipient_id=%s); the triggering action "
            "is unaffected.",
            kwargs.get("notification_type"),
            kwargs.get("recipient_id"),
        )
