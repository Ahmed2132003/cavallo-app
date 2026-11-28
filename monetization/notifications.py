"""
BE-11: subscription notifications, through the EXISTING notifications
pipeline (notifications.tasks.enqueue_notification -> dispatch_notification).

* One notification_type ("subscription"), no preference toggle, so it is
  always delivered (billing-critical), like system_announcement.
* deep_link_type="subscription_plans", target_id=None.
* The task is enqueued on commit, so a rolled-back caller never notifies.
* BE-08 reminder names (trial_ends_3d ...) stay as stored in
  SubscriptionReminder.event; EVENT_ALIASES maps them to the canonical
  names used for wording.
"""

import logging

from django.db import transaction

logger = logging.getLogger(__name__)

SUBSCRIPTION_DEEP_LINK = "subscription_plans"

EVENT_ALIASES = {
    "trial_ends_3d": "trial_ending_3d",
    "trial_ends_1d": "trial_ending_1d",
    "subscription_ends_3d": "subscription_ending_3d",
    "subscription_ends_1d": "subscription_ending_1d",
}

SUBSCRIPTION_EVENTS = frozenset(
    {
        "trial_ending_3d",
        "trial_ending_1d",
        "trial_expired",
        "purge_done",
        "subscription_activated",
        "subscription_ending_3d",
        "subscription_ending_1d",
        "subscription_expired",
    }
)


def canonical_event(event):
    return EVENT_ALIASES.get(event, event)


def notify_subscription_event(business, event):
    """
    Notify the owner of ``business`` about ``event``. Returns True when a
    notification was scheduled, False when the event is unknown or the
    business has no owner. Never raises for those two cases.
    """
    event = canonical_event(event)
    if event not in SUBSCRIPTION_EVENTS:
        logger.warning("Unknown subscription event %r (business=%s)", event, business.pk)
        return False
    recipient_id = getattr(business, "user_id", None)
    if recipient_id is None:
        logger.warning("Business %s has no owner; %s not sent", business.pk, event)
        return False

    # Lazy imports: keep monetization importable without the notifications app.
    from notifications.messages import render_notification
    from notifications.models import Notification
    from notifications.tasks import enqueue_notification

    params = {"event": event}
    title, body = render_notification("subscription", params, "en")
    payload = {
        "recipient_id": recipient_id,
        "notification_type": Notification.NotificationType.SUBSCRIPTION.value,
        "title": title,
        "body": body,
        "deep_link_type": SUBSCRIPTION_DEEP_LINK,
        "target_id": None,
        "params": params,
    }
    transaction.on_commit(lambda: enqueue_notification(**payload))
    return True