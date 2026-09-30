"""
Tests for notifications.tasks.dispatch_notification (Part P-079).

The task is called as a plain function (shared_task makes it directly
callable), same convention as chat/test_tasks.py. send_push_notification
is mocked: it is still a log-only stub (real FCM: P-081).

The suppression tests assert BOTH effects (no Notification row AND no
push attempt), not just the push.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from notifications.models import Notification, NotificationPreference
from notifications.tasks import (
    NOTIFICATION_TYPE_TO_PREFERENCE_FIELD,
    dispatch_notification,
)

User = get_user_model()

pytestmark = pytest.mark.django_db


def _make_user(username):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        account_type="customer",
    )


def _set_preference(user, **fields):
    NotificationPreference.objects.filter(user=user).update(**fields)


def test_mapping_covers_every_type_except_system_announcement():
    all_types = set(Notification.NotificationType.values)
    mapped = set(NOTIFICATION_TYPE_TO_PREFERENCE_FIELD)
    assert all_types - mapped == {"system_announcement"}
    assert mapped <= all_types


def test_mapping_values_are_real_preference_fields():
    real_fields = {f.name for f in NotificationPreference._meta.get_fields()}
    for field in NOTIFICATION_TYPE_TO_PREFERENCE_FIELD.values():
        assert field in real_fields


def test_mapping_matches_locked_contract():
    assert NOTIFICATION_TYPE_TO_PREFERENCE_FIELD == {
        "chat_message": "chat_notifications_enabled",
        "moderation_approved": "moderation_notifications_enabled",
        "moderation_rejected": "moderation_notifications_enabled",
        "new_follower": "social_notifications_enabled",
        "comment_on_content": "social_notifications_enabled",
        "new_like": "social_notifications_enabled",
        "new_share": "social_notifications_enabled",
        "new_rating": "social_notifications_enabled",
    }


@pytest.mark.parametrize(
    "notification_type, preference_field",
    sorted(NOTIFICATION_TYPE_TO_PREFERENCE_FIELD.items()),
)
def test_disabled_category_suppresses_row_and_push(notification_type, preference_field):
    user = _make_user(f"suppressed_{notification_type}")
    _set_preference(user, **{preference_field: False})

    with patch("notifications.tasks.send_push_notification") as mock_push:
        dispatch_notification(
            recipient_id=user.id,
            notification_type=notification_type,
            title="T",
            body="B",
        )

    assert Notification.objects.filter(recipient=user).count() == 0
    mock_push.assert_not_called()


def test_enabled_category_creates_row_and_pushes():
    user = _make_user("enabled_user")

    with patch("notifications.tasks.send_push_notification") as mock_push:
        dispatch_notification(
            recipient_id=user.id,
            notification_type="new_follower",
            title="New follower",
            body="Someone followed you",
            deep_link_type="business_profile",
            target_id=7,
        )

    notification = Notification.objects.get(recipient=user)
    assert notification.notification_type == "new_follower"
    assert notification.title == "New follower"
    assert notification.body == "Someone followed you"
    assert notification.deep_link_type == "business_profile"
    assert notification.target_id == 7
    mock_push.assert_called_once_with(
        user_id=user.id,
        title="New follower",
        body="Someone followed you",
        data={
            "type": "new_follower",
            "notification_id": notification.id,
            "deep_link_type": "business_profile",
            "target_id": 7,
        },
    )


def test_other_category_disabled_does_not_suppress():
    user = _make_user("other_category_user")
    _set_preference(user, social_notifications_enabled=False)

    with patch("notifications.tasks.send_push_notification") as mock_push:
        dispatch_notification(
            recipient_id=user.id,
            notification_type="chat_message",
            title="New message",
            body="hi",
        )

    assert Notification.objects.filter(recipient=user).count() == 1
    mock_push.assert_called_once()


def test_system_announcement_is_always_delivered():
    user = _make_user("announcement_user")
    _set_preference(
        user,
        chat_notifications_enabled=False,
        moderation_notifications_enabled=False,
        social_notifications_enabled=False,
    )

    with patch("notifications.tasks.send_push_notification") as mock_push:
        dispatch_notification(
            recipient_id=user.id,
            notification_type="system_announcement",
            title="Platform update",
            body="New factories section",
        )

    assert Notification.objects.filter(recipient=user).count() == 1
    mock_push.assert_called_once()


def test_unknown_recipient_does_nothing():
    with patch("notifications.tasks.send_push_notification") as mock_push:
        dispatch_notification(
            recipient_id=999999,
            notification_type="new_follower",
            title="T",
            body="B",
        )

    assert Notification.objects.count() == 0
    mock_push.assert_not_called()


def test_missing_preference_row_defaults_to_enabled():
    user = _make_user("no_pref_row_user")
    NotificationPreference.objects.filter(user=user).delete()

    with patch("notifications.tasks.send_push_notification") as mock_push:
        dispatch_notification(
            recipient_id=user.id,
            notification_type="comment_on_content",
            title="T",
            body="B",
        )

    assert Notification.objects.filter(recipient=user).count() == 1
    mock_push.assert_called_once()


def test_push_failure_keeps_in_app_row_and_does_not_raise():
    user = _make_user("push_fails_user")

    with patch(
        "notifications.tasks.send_push_notification",
        side_effect=RuntimeError("fcm down"),
    ):
        dispatch_notification(
            recipient_id=user.id,
            notification_type="new_like",
            title="T",
            body="B",
        )

    assert Notification.objects.filter(recipient=user).count() == 1


def test_invalid_notification_type_raises_and_sends_nothing():
    user = _make_user("bad_type_user")

    with patch("notifications.tasks.send_push_notification") as mock_push:
        with pytest.raises(ValueError):
            dispatch_notification(
                recipient_id=user.id,
                notification_type="not_a_real_type",
                title="T",
                body="B",
            )

    assert Notification.objects.filter(recipient=user).count() == 0
    mock_push.assert_not_called()


def test_deep_link_is_optional():
    user = _make_user("no_deep_link_user")

    with patch("notifications.tasks.send_push_notification") as mock_push:
        dispatch_notification(
            recipient_id=user.id,
            notification_type="system_announcement",
            title="T",
            body="B",
        )

    notification = Notification.objects.get(recipient=user)
    assert notification.deep_link_type == ""
    assert notification.target_id is None
    assert mock_push.call_args.kwargs["data"]["deep_link_type"] == ""
    assert mock_push.call_args.kwargs["data"]["target_id"] is None
