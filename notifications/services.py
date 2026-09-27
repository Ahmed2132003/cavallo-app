"""
Push-notification sending seam (Part P-072).

This is a deliberately minimal, early stub of what Phase 13 will build
out fully (real FCM SDK integration + device-token registration).
Firebase project credentials do not exist yet (Section 7 item 4,
BLOCKED), so this function currently just logs what it WOULD have
sent, at INFO level, with a clearly-marked TODO for Phase 13.

Signature is the documented seam chat.tasks.notify_offline_recipient
calls into — Phase 13 must not need to change this signature or touch
any of P-072's calling code, only replace this function's body.
"""

import logging

logger = logging.getLogger(__name__)


def send_push_notification(user_id: int, title: str, body: str, data: dict) -> None:
    # TODO(Phase 13): replace with real FCM SDK call once Firebase
    # project exists (Section 7 item 4) — signature should not need
    # to change.
    logger.info(
        "[STUB] Would send push to user %s: %s | body=%r data=%r",
        user_id,
        title,
        body,
        data,
    )