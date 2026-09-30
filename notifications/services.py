"""
Notification services (Part P-072 seam, completed in Part P-078).

Two INDEPENDENT functions live here, per architecture Sections 17/18:

* create_notification(...) - the in-app half. Writes one Notification
  row. Never sends a push.
* send_push_notification(...) - the push half. Sends (currently:
  logs) a push. Never writes a Notification row.

NEITHER FUNCTION MAY CALL THE OTHER. Part P-079's Celery task is the
one place that calls both together for a real event. Keeping them
apart means each is testable on its own, and a future admin-only
"resend push without recreating the in-app notification" tool needs
no refactor.
"""

import logging

from notifications.models import Notification

logger = logging.getLogger(__name__)


def create_notification(
    recipient,
    notification_type: str,
    title: str,
    body: str,
    deep_link_type: str | None = None,
    target_id: int | None = None,
) -> Notification:
    """
    Persist one in-app Notification for ``recipient`` and return it.

    ``notification_type`` answers WHY the notification exists;
    ``deep_link_type`` + ``target_id`` answer WHERE tapping it
    navigates (see the Notification model docstring). The two are
    validated independently.

    Raises ValueError (nothing is written) when:

    * ``notification_type`` is not a Notification.NotificationType value;
    * ``deep_link_type`` is given but is not a Notification.DeepLinkType
      value;
    * ``target_id`` is given without a ``deep_link_type`` (an id with no
      screen type cannot be navigated to).

    ``deep_link_type=None`` means "navigates nowhere" and is stored as
    an empty string.

    This function does NOT send a push and does NOT check the
    recipient's NotificationPreference; both belong to Part P-079's
    dispatch task.
    """
    if notification_type not in Notification.NotificationType.values:
        raise ValueError(f"Unknown notification_type: {notification_type!r}")

    if deep_link_type in (None, ""):
        if target_id is not None:
            raise ValueError("target_id requires a deep_link_type.")
        deep_link_type = ""
    elif deep_link_type not in Notification.DeepLinkType.values:
        raise ValueError(f"Unknown deep_link_type: {deep_link_type!r}")

    return Notification.objects.create(
        recipient=recipient,
        notification_type=notification_type,
        title=title,
        body=body,
        deep_link_type=deep_link_type,
        target_id=target_id,
    )


def send_push_notification(user_id: int, title: str, body: str, data: dict) -> None:
    """
    Push-sending seam. Signature frozen since Part P-072 (called by
    chat.tasks.notify_offline_recipient) - do not change it.

    Still a log-only stub. Does NOT create a Notification row; see
    create_notification().
    """
    # TODO(P-081): replace with the real FCM SDK call once the Firebase
    # project exists (Section 7 item 4) and device-token registration
    # lands. Signature must not change.
    logger.info(
        "[STUB] Would send push to user %s: %s | body=%r data=%r",
        user_id,
        title,
        body,
        data,
    )
