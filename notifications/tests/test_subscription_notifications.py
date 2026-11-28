"""BE-11: subscription notifications (ar/en, deep link, always delivered)."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from core.tests.test_integration_phase17 import _results
from monetization.constants import PURGE_GRACE_DAYS
from monetization.lifecycle import PAID_PURGE_GRACE_DAYS
from monetization.notifications import (
    EVENT_ALIASES,
    SUBSCRIPTION_EVENTS,
    notify_subscription_event,
)
from notifications.messages import render_notification
from notifications.models import Notification, NotificationPreference
from notifications.tasks import dispatch_notification

User = get_user_model()

pytestmark = pytest.mark.django_db

EVENTS = sorted(SUBSCRIPTION_EVENTS)


def _owner(username, language="ar"):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        account_type="customer",
    )
    User.objects.filter(pk=user.pk).update(preferred_language=language)
    user.refresh_from_db()
    # notify_subscription_event only needs pk + user_id.
    return user, SimpleNamespace(pk=1, user_id=user.pk)


def _notify(business, event, capture):
    with patch("notifications.tasks.dispatch_notification.delay") as delay:
        with capture(execute=True):
            result = notify_subscription_event(business, event)
    return result, delay


def _deliver(delay):
    assert delay.call_count == 1, delay.call_args_list
    with patch("notifications.tasks.send_push_notification") as push:
        dispatch_notification(**delay.call_args.kwargs)
    return push


def test_event_names_are_the_agreed_set():
    assert SUBSCRIPTION_EVENTS == {
        "trial_ending_3d",
        "trial_ending_1d",
        "trial_expired",
        "purge_done",
        "subscription_activated",
        "subscription_ending_3d",
        "subscription_ending_1d",
        "subscription_expired",
    }
    assert set(EVENT_ALIASES.values()) <= SUBSCRIPTION_EVENTS


@pytest.mark.parametrize("event", EVENTS)
def test_every_event_renders_in_arabic_and_english(event):
    en = render_notification("subscription", {"event": event}, "en")
    ar = render_notification("subscription", {"event": event}, "ar")
    assert en and ar
    assert all(en) and all(ar)
    assert en[0] != ar[0] and en[1] != ar[1]


def test_unknown_event_does_not_render():
    assert render_notification("subscription", {"event": "nope"}, "en") is None
    assert render_notification("subscription", {}, "ar") is None


def test_purge_warning_text_matches_the_grace_constants():
    # The wording hardcodes 2 / 60 days: this fails if a constant changes.
    assert PURGE_GRACE_DAYS == 2
    assert PAID_PURGE_GRACE_DAYS == 60
    trial = render_notification("subscription", {"event": "trial_expired"}, "en")
    paid = render_notification("subscription", {"event": "subscription_expired"}, "en")
    assert "2 days" in trial[1]
    assert "60 days" in paid[1]
    ar_trial = render_notification("subscription", {"event": "trial_expired"}, "ar")
    ar_paid = render_notification("subscription", {"event": "subscription_expired"}, "ar")
    assert "يومين" in ar_trial[1]
    assert "60" in ar_paid[1]


def test_enqueue_happens_only_on_commit(django_capture_on_commit_callbacks):
    _, business = _owner("owner_commit")
    with patch("notifications.tasks.dispatch_notification.delay") as delay:
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            assert notify_subscription_event(business, "trial_expired") is True
        assert delay.call_count == 0
        assert len(callbacks) == 1
        callbacks[0]()
        assert delay.call_count == 1


def test_enqueued_payload_has_deep_link_and_params(django_capture_on_commit_callbacks):
    user, business = _owner("owner_payload")
    result, delay = _notify(business, "trial_expired", django_capture_on_commit_callbacks)
    assert result is True
    kwargs = delay.call_args.kwargs
    assert kwargs["recipient_id"] == user.pk
    assert kwargs["notification_type"] == "subscription"
    assert kwargs["deep_link_type"] == "subscription_plans"
    assert kwargs["target_id"] is None
    assert kwargs["params"] == {"event": "trial_expired"}


@pytest.mark.parametrize(
    "stored, canonical",
    [
        ("trial_ends_3d", "trial_ending_3d"),
        ("trial_ends_1d", "trial_ending_1d"),
        ("subscription_ends_3d", "subscription_ending_3d"),
        ("subscription_ends_1d", "subscription_ending_1d"),
    ],
)
def test_be08_reminder_names_are_mapped(
    stored, canonical, django_capture_on_commit_callbacks
):
    _, business = _owner("owner_alias")
    _, delay = _notify(business, stored, django_capture_on_commit_callbacks)
    assert delay.call_args.kwargs["params"] == {"event": canonical}


def test_unknown_event_and_ownerless_business_send_nothing(
    django_capture_on_commit_callbacks,
):
    _, business = _owner("owner_unknown")
    result, delay = _notify(business, "bogus", django_capture_on_commit_callbacks)
    assert result is False and delay.call_count == 0
    ownerless = SimpleNamespace(pk=2, user_id=None)
    result, delay = _notify(
        ownerless, "trial_expired", django_capture_on_commit_callbacks
    )
    assert result is False and delay.call_count == 0


def test_arabic_owner_gets_arabic_row_and_deep_link_via_api(
    django_capture_on_commit_callbacks,
):
    user, business = _owner("owner_ar", "ar")
    _, delay = _notify(business, "trial_expired", django_capture_on_commit_callbacks)
    push = _deliver(delay)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get("/api/v1/notifications/")
    assert response.status_code == 200, response.content
    rows = _results(response)
    assert len(rows) == 1
    assert rows[0]["notification_type"] == "subscription"
    assert rows[0]["deep_link_type"] == "subscription_plans"
    assert rows[0]["target_id"] is None
    assert rows[0]["title"] == "انتهت تجربتك المجانية"
    kwargs = push.call_args.kwargs
    assert kwargs["data"]["deep_link_type"] == "subscription_plans"
    assert kwargs["localized"]["en"][0] == "Your free trial has ended"


def test_english_owner_gets_english_row(django_capture_on_commit_callbacks):
    user, business = _owner("owner_en", "en")
    _, delay = _notify(
        business, "subscription_activated", django_capture_on_commit_callbacks
    )
    _deliver(delay)
    row = Notification.objects.get(recipient=user)
    assert row.title == "Your subscription is active"


def test_critical_even_when_every_preference_is_off(django_capture_on_commit_callbacks):
    user, business = _owner("owner_prefs", "en")
    NotificationPreference.objects.filter(user=user).update(
        chat_notifications_enabled=False,
        moderation_notifications_enabled=False,
        social_notifications_enabled=False,
    )
    _, delay = _notify(business, "subscription_expired", django_capture_on_commit_callbacks)
    push = _deliver(delay)
    assert Notification.objects.filter(
        recipient=user, notification_type="subscription"
    ).count() == 1
    assert push.call_count == 1