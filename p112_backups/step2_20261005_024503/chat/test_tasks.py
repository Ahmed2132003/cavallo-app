"""
Tests for chat.tasks.notify_offline_recipient (Part P-072, updated P-079).

Runs the task as a plain function call (Celery's shared_task decorator
makes it directly callable without a worker) - same convention as
moderation/tests/test_tasks.py (P-039) and stories/tests/test_tasks.py
(P-048).

Part P-079: the task no longer calls send_push_notification directly; it
enqueues notifications.tasks.dispatch_notification. The dispatch_delay
fixture (root conftest.py) is the mocked dispatch_notification.delay.
"""

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


def test_notify_offline_recipient_resolves_other_participant_not_sender(
    dispatch_delay,
):
    sender = _make_user("task_sender")
    recipient = _make_user("task_recipient")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=sender)
    ConversationParticipant.objects.create(conversation=conversation, user=recipient)
    message = Message.objects.create(
        conversation=conversation, sender=sender, text="hello offline user"
    )

    notify_offline_recipient(message.id)

    dispatch_delay.assert_called_once_with(
        recipient_id=recipient.id,
        notification_type="chat_message",
        title="New message",
        body="hello offline user",
        deep_link_type="chat_thread",
        target_id=conversation.id,
    )


def test_notify_offline_recipient_handles_missing_message_gracefully(
    dispatch_delay,
):
    # No Message with this id at all - the task must log and return
    # without raising (best-effort, matching P-072's architecture rule).
    notify_offline_recipient(999999)

    dispatch_delay.assert_not_called()


def test_notify_offline_recipient_skips_when_no_other_participant(
    dispatch_delay,
):
    sender = _make_user("lonely_sender")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=sender)
    message = Message.objects.create(
        conversation=conversation, sender=sender, text="anyone there?"
    )

    notify_offline_recipient(message.id)

    dispatch_delay.assert_not_called()
