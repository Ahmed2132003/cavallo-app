"""
Service tests for notifications.services (Part P-078).

Proves create_notification() persists correctly, that
send_push_notification() keeps its frozen signature (its real FCM
behaviour is tested in test_push.py, Part P-081), and that the two
functions are truly independent (neither calls the other).
"""

import inspect
import logging
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from notifications.models import Notification
from notifications.services import create_notification, send_push_notification

User = get_user_model()

pytestmark = pytest.mark.django_db


def _make_user(username):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        account_type="customer",
    )


def test_create_notification_persists_a_row():
    user = _make_user("svc_recipient")

    notification = create_notification(
        recipient=user,
        notification_type="chat_message",
        title="New message",
        body="hello",
        deep_link_type="chat_thread",
        target_id=9,
    )

    stored = Notification.objects.get(pk=notification.pk)
    assert stored.recipient_id == user.id
    assert stored.notification_type == "chat_message"
    assert stored.title == "New message"
    assert stored.body == "hello"
    assert stored.deep_link_type == "chat_thread"
    assert stored.target_id == 9
    assert stored.is_read is False


def test_create_notification_without_deep_link_stores_blank_and_null():
    user = _make_user("svc_no_link")

    notification = create_notification(
        recipient=user,
        notification_type="system_announcement",
        title="Platform update",
        body="A new section was added.",
    )

    notification.refresh_from_db()
    assert notification.deep_link_type == ""
    assert notification.target_id is None


def test_create_notification_keeps_type_and_deep_link_distinct():
    user = _make_user("svc_distinct")

    first = create_notification(
        recipient=user,
        notification_type="new_follower",
        title="t",
        body="b",
        deep_link_type="business_profile",
        target_id=3,
    )
    second = create_notification(
        recipient=user,
        notification_type="new_rating",
        title="t",
        body="b",
        deep_link_type="business_profile",
        target_id=3,
    )

    assert first.notification_type != second.notification_type
    assert first.deep_link_type == second.deep_link_type == "business_profile"


def test_create_notification_rejects_unknown_notification_type():
    user = _make_user("svc_bad_type")

    with pytest.raises(ValueError):
        create_notification(
            recipient=user,
            notification_type="not_a_real_type",
            title="t",
            body="b",
        )

    assert Notification.objects.count() == 0


def test_create_notification_rejects_unknown_deep_link_type():
    user = _make_user("svc_bad_link")

    with pytest.raises(ValueError):
        create_notification(
            recipient=user,
            notification_type="new_like",
            title="t",
            body="b",
            deep_link_type="not_a_screen",
            target_id=1,
        )

    assert Notification.objects.count() == 0


def test_create_notification_rejects_target_id_without_deep_link_type():
    user = _make_user("svc_orphan_id")

    with pytest.raises(ValueError):
        create_notification(
            recipient=user,
            notification_type="new_like",
            title="t",
            body="b",
            target_id=5,
        )

    assert Notification.objects.count() == 0


def test_create_notification_never_sends_a_push():
    user = _make_user("svc_no_push")

    with patch("notifications.services.send_push_notification") as mock_send:
        create_notification(
            recipient=user,
            notification_type="new_like",
            title="t",
            body="b",
        )

    mock_send.assert_not_called()


def test_send_push_notification_never_creates_a_notification_row():
    user = _make_user("svc_push_only")

    with patch("notifications.services.create_notification") as mock_create:
        send_push_notification(
            user_id=user.id,
            title="t",
            body="b",
            data={"type": "chat_message"},
        )

    mock_create.assert_not_called()
    assert Notification.objects.count() == 0


def test_send_push_notification_without_tokens_does_not_raise_and_logs(caplog):
    with caplog.at_level(logging.INFO, logger="notifications.services"):
        result = send_push_notification(
            user_id=123,
            title="New message",
            body="hello",
            data={"type": "chat_message", "conversation_id": 1},
        )

    assert result is None
    assert "user 123 has no device tokens" in caplog.text


def test_send_push_notification_signature_is_frozen():
    # chat.tasks.notify_offline_recipient (P-072) depends on this
    # exact signature. Do not change it.
    parameters = list(inspect.signature(send_push_notification).parameters)

    assert parameters == ["user_id", "title", "body", "data"]
