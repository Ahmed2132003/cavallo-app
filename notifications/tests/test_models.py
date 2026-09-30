"""
Model tests for notifications.models (Part P-078).

Real test-database rows (pytest.mark.django_db), no mocks.
"""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from notifications.models import Notification, NotificationPreference

User = get_user_model()

pytestmark = pytest.mark.django_db


def _make_user(username):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        account_type="customer",
    )


def test_notification_stores_type_and_deep_link_pair_separately():
    user = _make_user("notif_recipient")

    notification = Notification.objects.create(
        recipient=user,
        notification_type=Notification.NotificationType.NEW_FOLLOWER,
        title="New follower",
        body="Someone followed your business.",
        deep_link_type=Notification.DeepLinkType.BUSINESS_PROFILE,
        target_id=42,
    )
    notification.refresh_from_db()

    assert notification.notification_type == "new_follower"
    assert notification.deep_link_type == "business_profile"
    assert notification.target_id == 42
    assert notification.notification_type != notification.deep_link_type


def test_two_different_types_can_deep_link_to_the_same_screen_type():
    user = _make_user("notif_same_screen")

    follower = Notification.objects.create(
        recipient=user,
        notification_type=Notification.NotificationType.NEW_FOLLOWER,
        title="New follower",
        body="body",
        deep_link_type=Notification.DeepLinkType.BUSINESS_PROFILE,
        target_id=7,
    )
    rating = Notification.objects.create(
        recipient=user,
        notification_type=Notification.NotificationType.NEW_RATING,
        title="New rating",
        body="body",
        deep_link_type=Notification.DeepLinkType.BUSINESS_PROFILE,
        target_id=7,
    )

    assert follower.notification_type != rating.notification_type
    assert follower.deep_link_type == rating.deep_link_type == "business_profile"


def test_deep_link_is_optional():
    user = _make_user("notif_no_link")

    notification = Notification.objects.create(
        recipient=user,
        notification_type=Notification.NotificationType.SYSTEM_ANNOUNCEMENT,
        title="Platform update",
        body="A new section was added.",
    )
    notification.refresh_from_db()

    assert notification.deep_link_type == ""
    assert notification.target_id is None


def test_is_read_defaults_false_and_created_at_is_set():
    user = _make_user("notif_defaults")

    notification = Notification.objects.create(
        recipient=user,
        notification_type=Notification.NotificationType.NEW_LIKE,
        title="New like",
        body="body",
    )

    assert notification.is_read is False
    assert notification.created_at is not None


def test_default_ordering_is_newest_first():
    user = _make_user("notif_ordering")
    first = Notification.objects.create(
        recipient=user,
        notification_type=Notification.NotificationType.NEW_LIKE,
        title="first",
        body="body",
    )
    second = Notification.objects.create(
        recipient=user,
        notification_type=Notification.NotificationType.NEW_LIKE,
        title="second",
        body="body",
    )

    assert list(Notification.objects.filter(recipient=user)) == [second, first]


def test_choice_values_are_the_locked_contract():
    # Parts P-079 (dispatch) and P-082 (Flutter notification center)
    # depend on these EXACT strings. Changing one is a breaking
    # change: update this test and PROJECT_PROGRESS.md deliberately.
    assert set(Notification.NotificationType.values) == {
        "chat_message",
        "moderation_approved",
        "moderation_rejected",
        "new_follower",
        "comment_on_content",
        "new_like",
        "new_share",
        "new_rating",
        "system_announcement",
    }
    assert set(Notification.DeepLinkType.values) == {
        "business_profile",
        "post_detail",
        "reel_detail",
        "product_detail",
        "chat_thread",
    }


def test_deleting_the_user_deletes_their_notifications():
    user = _make_user("notif_cascade")
    Notification.objects.create(
        recipient=user,
        notification_type=Notification.NotificationType.NEW_LIKE,
        title="t",
        body="b",
    )

    user.delete()

    assert Notification.objects.count() == 0


def test_preference_toggles_default_to_true():
    user = _make_user("pref_defaults")

    preference = NotificationPreference.objects.get(user=user)

    assert preference.chat_notifications_enabled is True
    assert preference.moderation_notifications_enabled is True
    assert preference.social_notifications_enabled is True


def test_preference_is_one_to_one_with_user():
    user = _make_user("pref_unique")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            NotificationPreference.objects.create(user=user)
