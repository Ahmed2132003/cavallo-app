"""
Tests for chat.tasks.notify_offline_recipient (Part P-072).

Runs the task as a plain function call (Celery's shared_task decorator
makes it directly callable without a worker) — same convention as
moderation/tests/test_tasks.py (P-039) and stories/tests/test_tasks.py
(P-048). notifications.services.send_push_notification is mocked here
because it's a documented stub (Phase 13 replaces it for real) — this
test only proves the task resolves the right recipient and calls the
seam with a reasonable, deep-link-ready payload, not that a real push
was sent.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from chat.models import Conversation, ConversationParticipant, Message
from chat.tasks import notify_offline_recipient

User = get_user_model()

pytestmark = pytest.mark.django_db


def _make_user(username):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        account_type="customer",
    )


def test_notify_offline_recipient_resolves_other_participant_not_sender():
    sender = _make_user("task_sender")
    recipient = _make_user("task_recipient")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=sender)
    ConversationParticipant.objects.create(conversation=conversation, user=recipient)
    message = Message.objects.create(
        conversation=conversation, sender=sender, text="hello offline user"
    )

    with patch("chat.tasks.send_push_notification") as mock_send:
        notify_offline_recipient(message.id)

    mock_send.assert_called_once_with(
        user_id=recipient.id,
        title="New message",
        body="hello offline user",
        data={
            "type": "chat_message",
            "conversation_id": conversation.id,
            "message_id": message.id,
        },
    )


def test_notify_offline_recipient_handles_missing_message_gracefully():
    # لا يوجد Message بهذا الـ id على الإطلاق — لازم الـ task يسجل
    # ويرجع من غير ما يعمل exception (best-effort, matching P-072's
    # architecture rule).
    with patch("chat.tasks.send_push_notification") as mock_send:
        notify_offline_recipient(999999)

    mock_send.assert_not_called()