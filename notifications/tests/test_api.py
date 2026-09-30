"""
API tests for Part P-082 (backend half) - the notification-center
endpoints: list, mark-read, preferences.

Mirrors devices/tests/test_api.py's conventions (force_authenticate,
APIClient, pytest.mark.django_db). The root conftest's autouse
``dispatch_delay`` fixture keeps every test off the real Redis broker;
the suppression tests below call the task function directly, which that
patch does not affect.
"""

from unittest import mock

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from notifications.models import Notification, NotificationPreference
from notifications.tasks import dispatch_notification

pytestmark = pytest.mark.django_db


def _make_user(email: str) -> User:
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type="customer",
    )


def _make_notification(recipient: User, **overrides) -> Notification:
    values = {
        "recipient": recipient,
        "notification_type": "new_follower",
        "title": "New follower",
        "body": "Someone started following your business.",
        "deep_link_type": "business_profile",
        "target_id": 7,
    }
    values.update(overrides)
    return Notification.objects.create(**values)


@pytest.fixture
def api_client():
    return APIClient()


def _list_url():
    return reverse("notifications:list")


def _read_url(pk):
    return reverse("notifications:mark-read", args=[pk])


def _prefs_url():
    return reverse("notifications:preferences")


class TestNotificationList:
    def test_url_is_the_documented_path(self):
        assert _list_url() == "/api/v1/notifications/"

    def test_unauthenticated_request_is_rejected(self, api_client):
        response = api_client.get(_list_url())

        assert response.status_code == 401

    def test_empty_list_for_user_without_notifications(self, api_client):
        user = _make_user("p082-empty@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.get(_list_url())

        assert response.status_code == 200
        assert response.json()["results"] == []
        assert response.json()["next"] is None

    def test_returns_only_the_callers_notifications(self, api_client):
        me = _make_user("p082-me@example.com")
        other = _make_user("p082-other@example.com")
        mine = _make_notification(me, title="mine")
        _make_notification(other, title="not mine")
        api_client.force_authenticate(user=me)

        response = api_client.get(_list_url())

        assert response.status_code == 200
        results = response.json()["results"]
        assert [item["id"] for item in results] == [mine.id]
        assert results[0]["title"] == "mine"

    def test_newest_first(self, api_client):
        user = _make_user("p082-order@example.com")
        for index in range(3):
            _make_notification(user, title=f"n{index}")
        api_client.force_authenticate(user=user)

        response = api_client.get(_list_url())

        titles = [item["title"] for item in response.json()["results"]]
        assert titles == ["n2", "n1", "n0"]

    def test_cursor_pagination_walks_all_pages(self, api_client):
        user = _make_user("p082-pages@example.com")
        for index in range(3):
            _make_notification(user, title=f"n{index}")
        api_client.force_authenticate(user=user)

        first = api_client.get(_list_url(), {"page_size": 2}).json()
        assert len(first["results"]) == 2
        assert first["next"] is not None

        second = api_client.get(first["next"]).json()
        assert len(second["results"]) == 1
        assert second["next"] is None

        ids = [item["id"] for item in first["results"] + second["results"]]
        assert len(set(ids)) == 3

    def test_item_exposes_exactly_the_documented_fields(self, api_client):
        user = _make_user("p082-fields@example.com")
        _make_notification(user)
        api_client.force_authenticate(user=user)

        item = api_client.get(_list_url()).json()["results"][0]

        assert set(item.keys()) == {
            "id",
            "notification_type",
            "title",
            "body",
            "deep_link_type",
            "target_id",
            "is_read",
            "created_at",
        }
        assert item["notification_type"] == "new_follower"
        assert item["deep_link_type"] == "business_profile"
        assert item["target_id"] == 7
        assert item["is_read"] is False


class TestMarkRead:
    def test_url_is_the_documented_path(self):
        assert _read_url(5) == "/api/v1/notifications/5/read/"

    def test_unauthenticated_request_is_rejected(self, api_client):
        user = _make_user("p082-read-anon@example.com")
        notification = _make_notification(user)

        response = api_client.patch(_read_url(notification.id))

        assert response.status_code == 401
        notification.refresh_from_db()
        assert notification.is_read is False

    def test_marks_own_notification_read(self, api_client):
        user = _make_user("p082-read-own@example.com")
        notification = _make_notification(user)
        api_client.force_authenticate(user=user)

        response = api_client.patch(_read_url(notification.id))

        assert response.status_code == 200
        assert response.json()["id"] == notification.id
        assert response.json()["is_read"] is True
        notification.refresh_from_db()
        assert notification.is_read is True

    def test_is_idempotent(self, api_client):
        user = _make_user("p082-read-twice@example.com")
        notification = _make_notification(user)
        api_client.force_authenticate(user=user)

        first = api_client.patch(_read_url(notification.id))
        second = api_client.patch(_read_url(notification.id))

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["is_read"] is True

    def test_other_users_notification_is_404_and_stays_unread(self, api_client):
        owner = _make_user("p082-read-owner@example.com")
        intruder = _make_user("p082-read-intruder@example.com")
        notification = _make_notification(owner)
        api_client.force_authenticate(user=intruder)

        response = api_client.patch(_read_url(notification.id))

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        notification.refresh_from_db()
        assert notification.is_read is False

    def test_nonexistent_notification_is_404(self, api_client):
        user = _make_user("p082-read-missing@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.patch(_read_url(999999))

        assert response.status_code == 404

    def test_ignores_forged_body_fields(self, api_client):
        owner = _make_user("p082-read-forge@example.com")
        other = _make_user("p082-read-forge-other@example.com")
        notification = _make_notification(owner)
        api_client.force_authenticate(user=owner)

        response = api_client.patch(
            _read_url(notification.id),
            {"is_read": False, "recipient": other.id, "title": "hacked"},
            format="json",
        )

        assert response.status_code == 200
        notification.refresh_from_db()
        assert notification.is_read is True
        assert notification.recipient_id == owner.id
        assert notification.title == "New follower"


class TestPreferences:
    def test_url_is_the_documented_path(self):
        assert _prefs_url() == "/api/v1/notifications/preferences/"

    def test_unauthenticated_get_is_rejected(self, api_client):
        assert api_client.get(_prefs_url()).status_code == 401

    def test_unauthenticated_patch_is_rejected(self, api_client):
        response = api_client.patch(
            _prefs_url(), {"chat_notifications_enabled": False}, format="json"
        )

        assert response.status_code == 401

    def test_get_returns_all_enabled_by_default(self, api_client):
        user = _make_user("p082-prefs-default@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.get(_prefs_url())

        assert response.status_code == 200
        assert response.json() == {
            "chat_notifications_enabled": True,
            "moderation_notifications_enabled": True,
            "social_notifications_enabled": True,
        }

    def test_get_creates_a_missing_row_instead_of_failing(self, api_client):
        user = _make_user("p082-prefs-missing@example.com")
        NotificationPreference.objects.filter(user=user).delete()
        api_client.force_authenticate(user=user)

        response = api_client.get(_prefs_url())

        assert response.status_code == 200
        assert response.json()["chat_notifications_enabled"] is True
        assert NotificationPreference.objects.filter(user=user).count() == 1

    def test_patch_updates_only_the_sent_field(self, api_client):
        user = _make_user("p082-prefs-one@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.patch(
            _prefs_url(), {"social_notifications_enabled": False}, format="json"
        )

        assert response.status_code == 200
        assert response.json() == {
            "chat_notifications_enabled": True,
            "moderation_notifications_enabled": True,
            "social_notifications_enabled": False,
        }
        prefs = NotificationPreference.objects.get(user=user)
        assert prefs.social_notifications_enabled is False
        assert prefs.chat_notifications_enabled is True
        assert prefs.moderation_notifications_enabled is True

    def test_patch_can_update_all_three_and_back(self, api_client):
        user = _make_user("p082-prefs-all@example.com")
        api_client.force_authenticate(user=user)

        off = api_client.patch(
            _prefs_url(),
            {
                "chat_notifications_enabled": False,
                "moderation_notifications_enabled": False,
                "social_notifications_enabled": False,
            },
            format="json",
        )
        on = api_client.patch(
            _prefs_url(),
            {
                "chat_notifications_enabled": True,
                "moderation_notifications_enabled": True,
                "social_notifications_enabled": True,
            },
            format="json",
        )

        assert off.status_code == 200
        assert not any(off.json().values())
        assert on.status_code == 200
        assert all(on.json().values())

    def test_patch_never_touches_another_users_row(self, api_client):
        me = _make_user("p082-prefs-me@example.com")
        other = _make_user("p082-prefs-other@example.com")
        api_client.force_authenticate(user=me)

        api_client.patch(
            _prefs_url(), {"chat_notifications_enabled": False}, format="json"
        )

        assert NotificationPreference.objects.get(user=other).chat_notifications_enabled
        assert not NotificationPreference.objects.get(
            user=me
        ).chat_notifications_enabled

    def test_patch_ignores_forged_user_and_id(self, api_client):
        me = _make_user("p082-prefs-forge@example.com")
        other = _make_user("p082-prefs-forge-other@example.com")
        other_prefs = NotificationPreference.objects.get(user=other)
        api_client.force_authenticate(user=me)

        response = api_client.patch(
            _prefs_url(),
            {
                "user": other.id,
                "id": other_prefs.id,
                "social_notifications_enabled": False,
            },
            format="json",
        )

        assert response.status_code == 200
        other_prefs.refresh_from_db()
        assert other_prefs.social_notifications_enabled is True
        assert other_prefs.user_id == other.id
        mine = NotificationPreference.objects.get(user=me)
        assert mine.social_notifications_enabled is False

    def test_patch_rejects_a_non_boolean_value(self, api_client):
        user = _make_user("p082-prefs-invalid@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.patch(
            _prefs_url(), {"chat_notifications_enabled": "maybe"}, format="json"
        )

        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert "chat_notifications_enabled" in error["fields"]
        prefs = NotificationPreference.objects.get(user=user)
        assert prefs.chat_notifications_enabled is True

    def test_put_is_not_allowed(self, api_client):
        user = _make_user("p082-prefs-put@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.put(
            _prefs_url(), {"chat_notifications_enabled": False}, format="json"
        )

        assert response.status_code == 405


class TestPreferenceSuppressionThroughTheApi:
    """
    Acceptance criterion: toggling a preference off through THIS API
    really stops that category's future notifications (P-079's
    suppression), while the other categories keep arriving.
    """

    def test_disabled_category_creates_no_notification(self, api_client):
        user = _make_user("p082-suppress@example.com")
        api_client.force_authenticate(user=user)
        api_client.patch(
            _prefs_url(), {"social_notifications_enabled": False}, format="json"
        )

        with mock.patch("notifications.tasks.send_push_notification") as push:
            dispatch_notification(
                recipient_id=user.id,
                notification_type="new_follower",
                title="New follower",
                body="Someone started following your business.",
                deep_link_type="business_profile",
                target_id=1,
            )

        assert api_client.get(_list_url()).json()["results"] == []
        push.assert_not_called()

    def test_other_categories_are_still_delivered(self, api_client):
        user = _make_user("p082-suppress-other@example.com")
        api_client.force_authenticate(user=user)
        api_client.patch(
            _prefs_url(), {"social_notifications_enabled": False}, format="json"
        )

        with mock.patch("notifications.tasks.send_push_notification"):
            dispatch_notification(
                recipient_id=user.id,
                notification_type="chat_message",
                title="New message",
                body="hello",
                deep_link_type="chat_thread",
                target_id=3,
            )

        results = api_client.get(_list_url()).json()["results"]
        assert len(results) == 1
        assert results[0]["notification_type"] == "chat_message"
