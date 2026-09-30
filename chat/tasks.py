"""
Offline-recipient notification dispatch (Part P-072, rewired in P-079).

Called (best-effort, never blocking the HTTP response) from
chat.views.MessageSendView.post() when the recipient was NOT connected
at broadcast time (per chat.consumers.presence_cache_key / P-070).
Resolves the actual recipient (the OTHER ConversationParticipant, not
the sender - a Conversation always has exactly two participants, see
chat/models.py) and hands the event to the single orchestration task
notifications.tasks.dispatch_notification, which checks the recipient's
NotificationPreference and then creates the in-app Notification row and
sends the push.

Part P-079: this task no longer calls send_push_notification directly.
Per the P-079 architecture rule, no notification source may bypass
dispatch_notification.

Idempotency (Section 5 rule 8): this task itself performs no writes; it
only enqueues dispatch_notification once. Re-running it for the same
message_id enqueues another dispatch (a possible duplicate notification).
See notifications/tasks.py for the honest MVP idempotency statement.

Part P-076: a media-only message has blank text, so the notification
body falls back to a short media label instead of an empty string.
"""

import logging

from celery import shared_task

from chat.models import ConversationParticipant, Message
from chat.serializers import shared_content_type_label
from notifications.tasks import dispatch_notification

logger = logging.getLogger(__name__)

_MEDIA_PUSH_BODIES = {
    Message.MediaType.IMAGE: "Sent a photo",
    Message.MediaType.VIDEO: "Sent a video",
}

# Part P-077: a message that only shares content has blank text too.
_SHARED_PUSH_BODIES = {
    "post": "Shared a post",
    "reel": "Shared a reel",
    "product": "Shared a product",
}


def _push_body(message):
    if message.text:
        return message.text[:120]
    if message.media_type:
        return _MEDIA_PUSH_BODIES.get(message.media_type, "")
    return _SHARED_PUSH_BODIES.get(shared_content_type_label(message), "")


@shared_task(name="chat.notify_offline_recipient", ignore_result=True)
def notify_offline_recipient(message_id):
    message = (
        Message.objects.filter(pk=message_id)
        .select_related("conversation", "sender")
        .first()
    )
    if message is None:
        # Race: message deleted/never existed by the time the task ran.
        # Best-effort by design - log and stop, no exception to retry.
        logger.warning(
            "notify_offline_recipient: Message id=%s no longer exists; "
            "skipping notification dispatch.",
            message_id,
        )
        return

    recipient_participant = (
        ConversationParticipant.objects.filter(conversation_id=message.conversation_id)
        .exclude(user_id=message.sender_id)
        .select_related("user")
        .first()
    )
    if recipient_participant is None:
        logger.warning(
            "notify_offline_recipient: no other participant found for "
            "conversation_id=%s (message_id=%s); skipping notification dispatch.",
            message.conversation_id,
            message_id,
        )
        return

    recipient = recipient_participant.user

    dispatch_notification.delay(
        recipient_id=recipient.id,
        notification_type="chat_message",
        title="New message",
        body=_push_body(message),
        deep_link_type="chat_thread",
        target_id=message.conversation_id,
    )
