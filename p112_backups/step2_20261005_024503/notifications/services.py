"""
Notification services (Part P-072 seam, completed in Part P-078).

Two INDEPENDENT functions live here, per architecture Sections 17/18:

* create_notification(...) - the in-app half. Writes one Notification
  row. Never sends a push.
* send_push_notification(...) - the push half. Sends a real FCM push
  to every device token the recipient has registered (Part P-081).
  Never writes a Notification row.

NEITHER FUNCTION MAY CALL THE OTHER. Part P-079's Celery task is the
one place that calls both together for a real event. Keeping them
apart means each is testable on its own, and a future admin-only
"resend push without recreating the in-app notification" tool needs
no refactor.
"""

import logging

import firebase_admin
from django.conf import settings
from firebase_admin import credentials, messaging

from devices.models import DeviceToken
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


def _get_firebase_app():
    """
    Return the initialised Firebase Admin app, or None when FCM is not
    configured (or fails to initialise).

    Reads ``settings.FCM_SERVICE_ACCOUNT_JSON_PATH``. While the Firebase
    project does not exist (architecture Section 7 item 4) that setting
    is blank and this returns None, so pushes are skipped with a log
    line instead of raising. Once the service-account key exists, only
    that setting changes - no code change.

    The app is created once and reused; ``firebase_admin.get_app()``
    raises ValueError when no default app exists yet.
    """
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    key_path = getattr(settings, "FCM_SERVICE_ACCOUNT_JSON_PATH", "")
    if not key_path:
        return None

    try:
        return firebase_admin.initialize_app(credentials.Certificate(key_path))
    except Exception:
        logger.exception(
            "send_push_notification: could not initialise Firebase from %r.",
            key_path,
        )
        return None


def _stringify_data(data: dict) -> dict[str, str]:
    """
    FCM data payloads only accept string keys AND string values.

    ``None`` values are omitted (e.g. ``target_id`` for a notification
    that navigates nowhere), so the client reads a missing key - or an
    empty ``deep_link_type`` - as "no deep link".
    """
    return {str(key): str(value) for key, value in data.items() if value is not None}


def send_push_notification(user_id: int, title: str, body: str, data: dict) -> None:
    """
    Push-sending seam. Signature frozen since Part P-072 (called by
    notifications.tasks.dispatch_notification) - do not change it.

    Sends one FCM message to every DeviceToken owned by ``user_id``.
    Does NOT create a Notification row; see create_notification().

    Never raises for an expected condition:

    * no registered tokens -> logged and skipped (a user who has not
      granted permission / not opened the app yet);
    * Firebase not configured -> logged and skipped;
    * one token fails (invalid / expired) -> logged, and the remaining
      tokens are still tried.
    """
    tokens = list(
        DeviceToken.objects.filter(user_id=user_id).values_list("token", flat=True)
    )
    if not tokens:
        logger.info("send_push_notification: user %s has no device tokens.", user_id)
        return

    app = _get_firebase_app()
    if app is None:
        logger.warning(
            "send_push_notification: Firebase is not configured; skipped push "
            "to user %s (%d device(s)).",
            user_id,
            len(tokens),
        )
        return

    payload = _stringify_data(data)
    notification = messaging.Notification(title=title, body=body)
    sent = 0
    for token in tokens:
        message = messaging.Message(
            token=token,
            notification=notification,
            data=payload,
            android=messaging.AndroidConfig(priority="high"),
        )
        try:
            messaging.send(message, app=app)
            sent += 1
        except Exception:
            # Log a token PREFIX only: the full value is credential-like.
            logger.exception(
                "send_push_notification: FCM send failed for user %s (token %s...).",
                user_id,
                token[:8],
            )

    logger.info(
        "send_push_notification: sent %d/%d push(es) to user %s.",
        sent,
        len(tokens),
        user_id,
    )
