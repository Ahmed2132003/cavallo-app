"""BE-11: subscription admin, expiry notifications, purge_done."""

from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from monetization.models import Subscription
from monetization.purge import purge_business_content
from monetization.tasks import expire_subscriptions
from monetization.tests.helpers import give_subscription
from payments.tests.factories import make_business

User = get_user_model()

pytestmark = pytest.mark.django_db

LIFECYCLE_NOTIFY = "monetization.lifecycle.notify_subscription_event"
PURGE_NOTIFY = "monetization.purge.notify_subscription_event"
CHANGELIST = "admin:monetization_subscription_changelist"


def _messages(response):
    """Admin messages of the (followed) response, to explain a failure."""
    return [str(m) for m in response.context["messages"]]


def _change_url(sub):
    return reverse("admin:monetization_subscription_change", args=[sub.pk])


def _extend(client, sub):
    return client.post(
        reverse(CHANGELIST),
        {"action": "extend_trial_7d", "_selected_action": [sub.pk]},
        follow=True,
    )


# ------------------------------------------------------------------ admin


def test_changelist_loads(admin_client):
    give_subscription(make_business("adm1"), "trialing", trial_days=3)
    assert admin_client.get(reverse(CHANGELIST)).status_code == 200


def test_add_and_delete_are_forbidden(admin_client):
    sub = give_subscription(make_business("adm2"), "trialing", trial_days=3)
    add = admin_client.get(reverse("admin:monetization_subscription_add"))
    delete = admin_client.get(
        reverse("admin:monetization_subscription_delete", args=[sub.pk])
    )
    assert add.status_code == 403
    assert delete.status_code == 403


def test_only_status_is_editable(admin_client):
    sub = give_subscription(make_business("adm3"), "trialing", trial_days=3)
    content = admin_client.get(_change_url(sub)).content.decode()
    assert 'name="status"' in content
    for name in ("plan", "quota_bytes", "trial_ends_at", "purge_at", "pending_plan"):
        assert f'name="{name}"' not in content


def test_manual_lock_clears_featured_effective(admin_client):
    sub = give_subscription(make_business("adm4"), "active", period_days=10)
    Subscription.objects.filter(pk=sub.pk).update(is_featured_effective=True)

    response = admin_client.post(
        _change_url(sub), {"status": "locked", "_save": "Save"}
    )

    assert response.status_code == 302
    sub.refresh_from_db()
    assert sub.status == "locked"
    assert sub.is_featured_effective is False


@pytest.mark.parametrize("target", ["active", "trialing"])
def test_status_cannot_be_manually_reactivated(admin_client, target):
    sub = give_subscription(make_business("adm5"), "locked", trial_days=-5)

    response = admin_client.post(
        _change_url(sub), {"status": target, "_save": "Save"}
    )

    assert response.status_code == 200  # form re-rendered with the error
    sub.refresh_from_db()
    assert sub.status == "locked"


def test_extend_trial_adds_seven_days_to_a_running_trial(admin_client):
    sub = give_subscription(make_business("adm6"), "trialing", trial_days=2)
    before = Subscription.objects.get(pk=sub.pk).trial_ends_at

    assert _extend(admin_client, sub).status_code == 200

    sub.refresh_from_db()
    assert sub.trial_ends_at == before + timedelta(days=7)


def test_extend_trial_restarts_from_now_when_the_date_passed(admin_client):
    sub = give_subscription(make_business("adm7"), "trialing", trial_days=-1)
    now = timezone.now()

    _extend(admin_client, sub)

    sub.refresh_from_db()
    assert now + timedelta(days=6) < sub.trial_ends_at < now + timedelta(days=8)


@pytest.mark.parametrize("status", ["active", "locked", "expired"])
def test_extend_trial_ignores_non_trialing(admin_client, status):
    sub = give_subscription(
        make_business("adm8"), status, trial_days=-5, period_days=-5
    )
    before = Subscription.objects.get(pk=sub.pk).trial_ends_at

    _extend(admin_client, sub)

    sub.refresh_from_db()
    assert sub.trial_ends_at == before
    assert sub.status == status


def test_extend_trial_needs_the_change_permission():
    sub = give_subscription(make_business("adm9"), "trialing", trial_days=2)
    before = Subscription.objects.get(pk=sub.pk).trial_ends_at
    staff = User.objects.create_user(
        username="viewer@example.com",
        email="viewer@example.com",
        password="testpass123",
        account_type="customer",
        is_staff=True,
    )
    staff.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="monetization", codename="view_subscription"
        )
    )
    client = Client()
    client.force_login(staff)

    _extend(client, sub)

    sub.refresh_from_db()
    assert sub.trial_ends_at == before


# ------------------------------------------------- expiry sweep notifies


@pytest.mark.parametrize(
    "status, offsets, event",
    [
        ("trialing", {"trial_days": -1}, "trial_expired"),
        ("active", {"period_days": -1}, "subscription_expired"),
    ],
)
def test_sweep_notifies_once_per_ended_period(status, offsets, event):
    business = make_business("sweep")
    give_subscription(business, status, **offsets)

    with mock.patch(LIFECYCLE_NOTIFY) as notify:
        expire_subscriptions()
        expire_subscriptions()

    notify.assert_called_once()
    assert notify.call_args.args[0].pk == business.pk
    assert notify.call_args.args[1] == event


def test_a_failing_notification_does_not_stop_the_sweep():
    first = give_subscription(make_business("fail-a"), "trialing", trial_days=-1)
    second = give_subscription(
        make_business("fail-b", phone_number="+201001234568"),
        "trialing",
        trial_days=-1,
    )

    with mock.patch(LIFECYCLE_NOTIFY, side_effect=RuntimeError("boom")):
        counts = expire_subscriptions()

    assert counts == {"renewed": 0, "expired": 0, "locked": 2}
    for sub in (first, second):
        sub.refresh_from_db()
        assert sub.status == "locked"


def test_sweep_reaches_the_notification_pipeline(django_capture_on_commit_callbacks):
    business = make_business("pipeline")
    give_subscription(business, "trialing", trial_days=-1)

    with mock.patch("notifications.tasks.dispatch_notification.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            expire_subscriptions()

    delay.assert_called_once()
    kwargs = delay.call_args.kwargs
    assert kwargs["recipient_id"] == business.user_id
    assert kwargs["notification_type"] == "subscription"
    assert kwargs["params"] == {"event": "trial_expired"}


# ------------------------------------------------------------ purge_done


def _due_locked_business(suffix):
    business = make_business(suffix)
    sub = give_subscription(business, "locked", trial_days=-10)
    Subscription.objects.filter(pk=sub.pk).update(
        purge_at=timezone.now() - timedelta(days=8)
    )
    return business


def test_purge_done_is_sent_only_after_commit_and_only_once(
    django_capture_on_commit_callbacks,
):
    business = _due_locked_business("purge")

    with mock.patch(PURGE_NOTIFY) as notify:
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            report = purge_business_content(business)
        assert report.purged is True
        assert notify.call_count == 0  # nothing before the commit
        for callback in callbacks:
            callback()
        notify.assert_called_once()
        assert notify.call_args.args[0].pk == business.pk
        assert notify.call_args.args[1] == "purge_done"

        with django_capture_on_commit_callbacks(execute=True):
            again = purge_business_content(business)
        assert again.skipped_reason == "already_purged"
        notify.assert_called_once()


def test_dry_run_never_notifies(django_capture_on_commit_callbacks):
    business = _due_locked_business("dry")

    with mock.patch(PURGE_NOTIFY) as notify:
        with django_capture_on_commit_callbacks(execute=True):
            report = purge_business_content(business, dry_run=True)

    assert report.purged is False and report.skipped_reason == ""
    notify.assert_not_called()


def test_a_running_trial_is_never_purged_or_notified(django_capture_on_commit_callbacks):
    business = make_business("running")
    give_subscription(business, "trialing", trial_days=5)

    with mock.patch(PURGE_NOTIFY) as notify:
        with django_capture_on_commit_callbacks(execute=True):
            report = purge_business_content(business)

    assert report.skipped_reason == "not_ended"
    notify.assert_not_called()