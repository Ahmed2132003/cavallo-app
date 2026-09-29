"""
Offline-recipient push-notification dispatch (Part P-072).

Called (best-effort, never blocking the HTTP response) from
chat.views.MessageSendView.post() when the recipient was NOT connected
at broadcast time (per chat.consumers.presence_cache_key / P-070).
Resolves the actual recipient (the OTHER ConversationParticipant, not
the sender — a Conversation always has exactly two participants, see
chat/models.py) and calls notifications.services.send_push_notification(),
which is currently a log-only stub (Phase 13 replaces its body; this
task's own code must not need to change when that happens).

Idempotency (Section 5 rule 8): re-running this task for the same
message_id sends the same "would-be push" log line again — it performs
no writes, so there is nothing to double-apply. Genuine push delivery
itself remains unverified end-to-end pending Firebase credentials
(Section 7 item 4); see PROJECT_PROGRESS.md.

Part P-076: a media-only message has blank text, so the push body falls
back to a short media label instead of an empty string.
"""

import logging

from celery import shared_task

from chat.models import ConversationParticipant, Message
from notifications.services import send_push_notification

logger = logging.getLogger(__name__)

_MEDIA_PUSH_BODIES = {
    Message.MediaType.IMAGE: "Sent a photo",
    Message.MediaType.VIDEO: "Sent a video",
}


@shared_task(name="chat.notify_offline_recipient", ignore_result=True)
def notify_offline_recipient(message_id):
    message = (
        Message.objects.filter(pk=message_id)
        .select_related("conversation", "sender")
        .first()
    )
    if message is None:
        # Race: message deleted/never existed by the time the task ran.
        # Best-effort by design — log and stop, no exception to retry.
        logger.warning(
            "notify_offline_recipient: Message id=%s no longer exists; "
            "skipping push dispatch.",
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
            "conversation_id=%s (message_id=%s); skipping push dispatch.",
            message.conversation_id,
            message_id,
        )
        return

    recipient = recipient_participant.user

    send_push_notification(
        user_id=recipient.id,
        title="New message",
        body=message.text[:120] or _MEDIA_PUSH_BODIES.get(message.media_type, ""),
        data={
            "type": "chat_message",
            "conversation_id": message.conversation_id,
            "message_id": message.id,
        },
    )
