"""BE-08 - storage subscription lifecycle."""

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from celery.schedules import crontab
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from monetization.lifecycle import send_due_reminders
from monetization.models import Plan, StoredFile, Subscription
from monetization.services import custom_quota_bytes
from monetization.tasks import expire_subscriptions
from payments.models import Subscription as Payment
from payments.storage_activation import activate_storage_subscription
from payments.tests.factories import make_business, make_storage_plan

pytestmark = pytest.mark.django_db

GB = 1024**3
URL = "/api/v1/payments/subscriptions/checkout/"
NOTIFY = "monetization.lifecycle.notify_subscription_event"


def _plans():
    return (
        make_storage_plan("pro", storage_gb=10),
        make_storage_plan("max", storage_gb=50),
    )


def _sub(business, plan, **overrides):
    now = timezone.now()
    values = {
        "plan": plan,
        "status": "active",
        "started_at": now,
        "trial_ends_at": None,
        "current_period_start": now - timedelta(days=10),
        "current_period_end": now + timedelta(days=20),
        "quota_bytes": plan.storage_bytes,
        "custom_gb": None,
        "purge_at": None,
        "pending_plan": None,
        "pending_quota_bytes": None,
        "is_featured_effective": False,
        "canceled_at": None,
    }
    values.update(overrides)
    sub, _ = Subscription.objects.update_or_create(
        business=business, defaults=values
    )
    return sub


def _buy(business, plan, gb=None):
    return activate_storage_subscription(business, plan, gb, SimpleNamespace(pk=1))


def _use(business, size):
    StoredFile.objects.create(
        business=business,
        model="content.Reel",
        object_id=999999,
        field="video",
        file_name="be08.mp4",
        size_bytes=size,
    )


def _checkout(business, body):
    client = APIClient()
    client.force_authenticate(user=business.user)
    return client.post(URL, body, format="json")


# ---------------------------------------------------------------- expiry


def test_trial_becomes_locked_and_keeps_its_purge_date():
    pro, _ = _plans()
    business = make_business("owner")
    end = timezone.now() - timedelta(hours=1)
    purge = end + timedelta(days=2)
    sub = _sub(
        business,
        pro,
        status="trialing",
        trial_ends_at=end,
        current_period_end=None,
        purge_at=purge,
        is_featured_effective=True,
    )

    assert expire_subscriptions() == {"renewed": 0, "expired": 0, "locked": 1}

    sub.refresh_from_db()
    assert sub.status == "locked"
    assert sub.purge_at == purge
    assert sub.is_featured_effective is False


def test_active_becomes_expired_with_60_day_purge_and_is_idempotent():
    pro, _ = _plans()
    end = timezone.now() - timedelta(hours=1)
    sub = _sub(
        make_business("owner"),
        pro,
        current_period_end=end,
        is_featured_effective=True,
    )

    assert expire_subscriptions() == {"renewed": 0, "expired": 1, "locked": 0}
    sub.refresh_from_db()
    assert sub.status == "expired"
    assert sub.purge_at == end + timedelta(days=60)
    assert sub.is_featured_effective is False
    updated_at = sub.updated_at

    assert expire_subscriptions() == {"renewed": 0, "expired": 0, "locked": 0}
    sub.refresh_from_db()
    assert sub.updated_at == updated_at


def test_running_subscription_is_untouched():
    pro, _ = _plans()
    sub = _sub(make_business("owner"), pro)

    assert expire_subscriptions() == {"renewed": 0, "expired": 0, "locked": 0}
    sub.refresh_from_db()
    assert sub.status == "active"


# ------------------------------------------------------------ activation


def test_renewal_extends_from_period_end_and_keeps_usage():
    pro, _ = _plans()
    business = make_business("owner")
    before = _sub(business, pro)
    _use(business, GB)

    after = _buy(business, pro)

    assert after.current_period_end == before.current_period_end + timedelta(days=30)
    assert after.current_period_start == before.current_period_start
    assert StoredFile.objects.get(business=business, object_id=999999).size_bytes == GB


def test_payment_after_expiry_starts_now_and_cancels_purge():
    pro, _ = _plans()
    business = make_business("owner")
    now = timezone.now()
    _sub(
        business,
        pro,
        status="expired",
        current_period_end=now - timedelta(days=5),
        purge_at=now + timedelta(days=55),
    )

    sub = _buy(business, pro)

    assert sub.status == "active"
    assert sub.purge_at is None
    assert sub.current_period_start >= now
    assert sub.current_period_end - sub.current_period_start == timedelta(days=30)


def test_upgrade_is_immediate_and_updates_featured():
    pro, big = _plans()
    Plan.objects.filter(pk=big.pk).update(is_featured=True)
    big.refresh_from_db()
    business = make_business("owner")
    _sub(business, pro)
    now = timezone.now()

    sub = _buy(business, big)

    assert sub.plan_id == big.pk
    assert sub.quota_bytes == 50 * GB
    assert sub.is_featured_effective is True
    assert sub.current_period_start >= now
    assert sub.pending_plan_id is None


def test_downgrade_is_pending_then_applied_at_period_end():
    pro, big = _plans()
    business = make_business("owner")
    before = _sub(business, big)

    sub = _buy(business, pro)

    assert sub.plan_id == big.pk
    assert sub.quota_bytes == 50 * GB
    assert sub.pending_plan_id == pro.pk
    assert sub.current_period_end == before.current_period_end

    end = timezone.now() - timedelta(minutes=1)
    Subscription.objects.filter(pk=sub.pk).update(current_period_end=end)

    assert expire_subscriptions() == {"renewed": 1, "expired": 0, "locked": 0}
    assert expire_subscriptions() == {"renewed": 0, "expired": 0, "locked": 0}
    sub.refresh_from_db()
    assert sub.status == "active"
    assert sub.plan_id == pro.pk
    assert sub.quota_bytes == 10 * GB
    assert sub.pending_plan_id is None
    assert sub.current_period_start == end
    assert sub.current_period_end == end + timedelta(days=30)


def test_custom_downgrade_keeps_the_exact_paid_quota():
    _, big = _plans()
    custom = make_storage_plan(
        "infinity", price_egp=None, storage_gb=None, is_custom=True
    )
    business = make_business("owner")
    _sub(business, big)

    sub = _buy(business, custom, "20.5")

    assert sub.pending_quota_bytes == custom_quota_bytes("20.5")
    Subscription.objects.filter(pk=sub.pk).update(
        current_period_end=timezone.now() - timedelta(minutes=1)
    )
    expire_subscriptions()
    sub.refresh_from_db()
    assert sub.quota_bytes == custom_quota_bytes("20.5")
    assert sub.custom_gb == 20


def test_renewal_keeps_the_pending_plan():
    pro, big = _plans()
    business = make_business("owner")
    _sub(business, big)
    _buy(business, pro)

    sub = _buy(business, big)

    assert sub.pending_plan_id == pro.pk


# -------------------------------------------------------------- checkout


@pytest.fixture
def fake_gateway(settings):
    settings.PAYMENT_GATEWAY = "payments.tests.fake_gateway.FakeGateway"


def test_downgrade_over_usage_is_blocked_before_payment(fake_gateway):
    pro, big = _plans()
    business = make_business("owner")
    _sub(business, big)
    _use(business, 11 * GB)

    response = _checkout(business, {"plan": "pro"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "downgrade_blocked_usage"
    assert Payment.objects.count() == 0


def test_downgrade_that_fits_is_allowed(fake_gateway):
    _, big = _plans()
    business = make_business("owner")
    _sub(business, big)

    assert _checkout(business, {"plan": "pro"}).status_code == 201


def test_second_downgrade_while_one_is_pending_is_blocked(fake_gateway):
    pro, big = _plans()
    business = make_business("owner")
    _sub(business, big, pending_plan=pro, pending_quota_bytes=10 * GB)

    response = _checkout(business, {"plan": "pro"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "downgrade_already_pending"


def test_renewing_the_same_plan_is_never_blocked(fake_gateway):
    pro, _ = _plans()
    business = make_business("owner")
    _sub(business, pro)
    _use(business, 11 * GB)

    assert _checkout(business, {"plan": "pro"}).status_code == 201


# ------------------------------------------------------------- reminders


@pytest.mark.parametrize(
    "status,hours,event",
    [
        ("trialing", 60, "trial_ends_3d"),
        ("trialing", 12, "trial_ends_1d"),
        ("active", 60, "subscription_ends_3d"),
        ("active", 12, "subscription_ends_1d"),
    ],
)
def test_reminder_is_sent_once(status, hours, event):
    pro, _ = _plans()
    business = make_business("owner")
    end = timezone.now() + timedelta(hours=hours)
    field = "trial_ends_at" if status == "trialing" else "current_period_end"
    _sub(business, pro, status=status, **{field: end})

    with mock.patch(NOTIFY) as notify:
        assert send_due_reminders() == 1
        assert send_due_reminders() == 0

    notify.assert_called_once()
    assert notify.call_args.args[1] == event


def test_no_reminder_when_far_away_or_already_paid():
    pro, big = _plans()
    _sub(make_business("far"), pro)
    _sub(
        make_business("paid", phone_number="+201001234568"),
        big,
        current_period_end=timezone.now() + timedelta(days=1),
        pending_plan=pro,
    )

    assert send_due_reminders() == 0


def test_a_new_period_gets_new_reminders():
    pro, _ = _plans()
    sub = _sub(
        make_business("owner"),
        pro,
        current_period_end=timezone.now() + timedelta(days=2),
    )
    assert send_due_reminders() == 1
    Subscription.objects.filter(pk=sub.pk).update(
        current_period_end=timezone.now() + timedelta(days=2, hours=1)
    )

    assert send_due_reminders() == 1


def test_beat_registration():
    schedule = settings.CELERY_BEAT_SCHEDULE
    expiry = schedule["expire-storage-subscriptions"]
    reminders = schedule["send-subscription-reminders"]
    assert expiry["task"] == "monetization.expire_subscriptions"
    assert expiry["schedule"] == crontab(minute="*/15")
    assert reminders["task"] == "monetization.send_subscription_reminders"
    assert reminders["schedule"] == crontab(minute=10)