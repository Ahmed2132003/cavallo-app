"""
P-096 (step 3) permission / IDOR sweep for the notifications app.

Routes: GET /notifications/ (own list), PATCH /notifications/{id}/read/
(mark ONE OWN notification read), GET + PATCH /notifications/preferences/
(own toggles). There is no create, delete or detail route.

Category 1: every route -> 401 envelope (no token and garbage token);
            nothing changes.
Category 2: user B can never read, mark read, or change user A's
            notification or preferences (the object is a 404 for B, and
            A's row is re-read from the DB to prove it is untouched);
            a spoofed `user` / `recipient` in the body is ignored; the
            mark-read endpoint ignores its body entirely.
Category 3: no capability-gated route exists in this app (notifications
            are only ever created by notifications/tasks.py).
"""

import pytest

from core.tests.sweep_factories import client_for, garbage_token_client, make_user
from core.tests.sweep_helpers import assert_not_found, assert_unauthenticated
from notifications.models import Notification, NotificationPreference

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/notifications/"
PREFS_URL = "/api/v1/notifications/preferences/"


def _read_url(pk):
    return f"/api/v1/notifications/{pk}/read/"


def _notify(user, title="hello"):
    return Notification.objects.create(
        recipient=user,
        notification_type=Notification.NotificationType.NEW_FOLLOWER,
        title=title,
        body="body text",
    )


def _prefs(user):
    return NotificationPreference.objects.get(user=user)


@pytest.mark.parametrize("client_factory", [client_for, garbage_token_client])
@pytest.mark.parametrize(
    "method,url,payload",
    [
        ("get", LIST_URL, None),
        ("get", PREFS_URL, None),
        ("patch", PREFS_URL, {"chat_notifications_enabled": False}),
        ("patch", "READ", {}),
    ],
    ids=["list", "prefs-get", "prefs-patch", "mark-read"],
)
def test_unauthenticated_gets_401_and_changes_nothing(
    method, url, payload, client_factory
):
    owner = make_user()
    notification = _notify(owner)
    if url == "READ":
        url = _read_url(notification.pk)

    response = getattr(client_factory(), method)(url, payload or {}, format="json")

    assert_unauthenticated(response)
    notification.refresh_from_db()
    assert notification.is_read is False
    assert _prefs(owner).chat_notifications_enabled is True


def test_list_only_ever_contains_the_callers_own_notifications():
    me, other = make_user(), make_user()
    mine = _notify(me, "mine")
    theirs = _notify(other, "theirs")

    mine_ids = [item["id"] for item in client_for(me).get(LIST_URL).json()["results"]]
    their_ids = [
        item["id"] for item in client_for(other).get(LIST_URL).json()["results"]
    ]

    assert mine_ids == [mine.pk]
    assert their_ids == [theirs.pk]


def test_other_users_mark_read_is_404_and_leaves_the_notification_unread():
    owner, attacker = make_user(), make_user()
    notification = _notify(owner)

    response = client_for(attacker).patch(_read_url(notification.pk), {}, format="json")

    assert_not_found(response)
    notification.refresh_from_db()
    assert notification.is_read is False


def test_unknown_notification_is_404_with_envelope():
    response = client_for(make_user()).patch(_read_url(999999), {}, format="json")

    assert_not_found(response)


def test_mark_read_ignores_the_body_and_changes_nothing_else():
    owner, victim = make_user(), make_user()
    notification = _notify(owner, "original title")

    response = client_for(owner).patch(
        _read_url(notification.pk),
        {
            "is_read": False,
            "title": "hacked",
            "recipient": victim.pk,
            "target_id": 1,
        },
        format="json",
    )

    assert response.status_code == 200
    notification.refresh_from_db()
    assert notification.is_read is True
    assert notification.title == "original title"
    assert notification.recipient_id == owner.pk
    assert notification.target_id is None


def test_other_users_preferences_patch_never_touches_my_row():
    me, other = make_user(), make_user()

    response = client_for(other).patch(
        PREFS_URL,
        {"chat_notifications_enabled": False, "user": me.pk, "id": _prefs(me).pk},
        format="json",
    )

    assert response.status_code == 200
    assert _prefs(me).chat_notifications_enabled is True
    assert _prefs(other).chat_notifications_enabled is False
    assert NotificationPreference.objects.count() == 2


def test_preferences_get_always_returns_the_callers_own_row():
    me, other = make_user(), make_user()
    NotificationPreference.objects.filter(user=other).update(
        social_notifications_enabled=False
    )

    mine = client_for(me).get(PREFS_URL).json()
    theirs = client_for(other).get(PREFS_URL).json()

    assert mine["social_notifications_enabled"] is True
    assert theirs["social_notifications_enabled"] is False


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_no_create_or_modify_handler_exists_on_the_list_route(method):
    me = make_user()
    notification = _notify(me)

    response = getattr(client_for(me), method)(LIST_URL, {}, format="json")

    assert response.status_code == 405
    assert Notification.objects.count() == 1
    notification.refresh_from_db()
    assert notification.is_read is False


@pytest.mark.parametrize("method", ["get", "post", "put", "delete"])
def test_mark_read_route_only_accepts_patch(method):
    me = make_user()
    notification = _notify(me)

    response = getattr(client_for(me), method)(_read_url(notification.pk))

    assert response.status_code == 405
    notification.refresh_from_db()
    assert notification.is_read is False
    assert Notification.objects.filter(pk=notification.pk).exists()


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_no_detail_route_exists_for_a_notification(method):
    me = make_user()
    notification = _notify(me)

    response = getattr(client_for(me), method)(f"{LIST_URL}{notification.pk}/")

    assert response.status_code == 404
    assert Notification.objects.filter(pk=notification.pk).exists()
