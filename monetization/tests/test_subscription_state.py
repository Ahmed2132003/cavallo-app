"""BE-02 tests: auto-create, backfill and effective_state."""

import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from businesses.services import create_business_profile
from monetization import constants
from monetization.models import Plan, Subscription
from monetization.services import (
    create_free_subscription,
    effective_state,
    get_subscription,
    is_locked,
)

pytestmark = pytest.mark.django_db

User = get_user_model()
backfill_migration = importlib.import_module(
    "monetization.migrations.0004_backfill_free_subscriptions"
)


def _make_business(suffix="a"):
    email = f"subscription-state-{suffix}@example.com"
    user = User.objects.create_user(
        username=email,
        email=email,
        password="testpass123",
        account_type="business",
    )
    return create_business_profile(
        user=user,
        business_name=f"Subscription Trader {suffix}",
        business_type="trader",
        country="EG",
        city="Ismailia",
    )


# ---- auto-create ----------------------------------------------------


def test_creating_a_business_creates_a_free_trial_subscription():
    business = _make_business()

    sub = Subscription.objects.get(business=business)

    assert sub.plan.code == "free"
    assert sub.status == "trialing"
    assert sub.quota_bytes == 100 * constants.BYTES_PER_MB
    assert sub.trial_ends_at == sub.started_at + timedelta(days=15)
    assert sub.purge_at == sub.trial_ends_at + timedelta(days=2)
    assert sub.current_period_start is None and sub.current_period_end is None
    assert sub.custom_gb is None
    assert sub.pending_plan is None
    assert sub.canceled_at is None
    assert sub.is_featured_effective is False


def test_create_free_subscription_is_idempotent():
    business = _make_business()
    first = Subscription.objects.get(business=business)

    again = create_free_subscription(business)

    assert again.pk == first.pk
    assert Subscription.objects.filter(business=business).count() == 1


def test_missing_free_plan_skips_creation_without_error():
    Plan.objects.filter(code="free").delete()

    business = _make_business()

    assert get_subscription(business) is None


def test_saving_an_existing_business_does_not_recreate_a_subscription():
    business = _make_business()
    Subscription.objects.filter(business=business).delete()

    business.save()

    assert Subscription.objects.filter(business=business).count() == 0


def test_only_one_subscription_per_business():
    business = _make_business()
    plan = Plan.objects.get(code="pro")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Subscription.objects.create(
                business=business,
                plan=plan,
                started_at=timezone.now(),
                quota_bytes=1,
            )


def test_get_subscription_returns_the_row_or_none():
    business = _make_business()
    assert get_subscription(business).business_id == business.pk
    Subscription.objects.filter(business=business).delete()
    assert get_subscription(business) is None


# ---- backfill -------------------------------------------------------


def test_backfill_creates_free_subscriptions_starting_now():
    old_a = _make_business("a")
    old_b = _make_business("b")
    Subscription.objects.all().delete()

    before = timezone.now()
    backfill_migration.backfill_free_subscriptions(django_apps, None)
    after = timezone.now()

    for business in (old_a, old_b):
        sub = Subscription.objects.get(business=business)
        assert sub.plan.code == "free"
        assert sub.status == "trialing"
        assert before <= sub.started_at <= after
        assert sub.trial_ends_at == sub.started_at + timedelta(days=15)
        assert sub.purge_at == sub.trial_ends_at + timedelta(days=2)
        assert sub.quota_bytes == 100 * constants.BYTES_PER_MB


def test_backfill_is_idempotent_and_keeps_existing_rows():
    keep = _make_business("keep")
    gap = _make_business("gap")
    Subscription.objects.filter(business=gap).delete()
    kept_pk = Subscription.objects.get(business=keep).pk

    backfill_migration.backfill_free_subscriptions(django_apps, None)
    backfill_migration.backfill_free_subscriptions(django_apps, None)

    assert Subscription.objects.get(business=keep).pk == kept_pk
    assert Subscription.objects.count() == 2


def test_backfill_without_free_plan_is_a_noop():
    business = _make_business()
    Subscription.objects.filter(business=business).delete()
    Plan.objects.filter(code="free").delete()

    backfill_migration.backfill_free_subscriptions(django_apps, None)

    assert Subscription.objects.count() == 0


# ---- effective_state ------------------------------------------------


def test_trial_in_progress():
    business = _make_business()
    sub = get_subscription(business)
    now = sub.started_at + timedelta(days=5)

    state = effective_state(business, now=now)

    assert state == {
        "state": "trialing",
        "days_left": 10,
        "locked": False,
        "purge_at": sub.purge_at,
    }
    assert is_locked(business, now=now) is False


def test_days_left_rounds_up():
    business = _make_business()
    sub = get_subscription(business)

    state = effective_state(business, now=sub.started_at + timedelta(days=14.5))

    assert state["days_left"] == 1


def test_trial_ended_is_expired_and_locked_even_if_stored_status_is_trialing():
    business = _make_business()
    sub = get_subscription(business)
    assert sub.status == "trialing"
    now = sub.trial_ends_at + timedelta(hours=1)

    state = effective_state(business, now=now)

    assert state["state"] == "expired"
    assert state["locked"] is True
    assert state["days_left"] == 0
    assert state["purge_at"] == sub.purge_at
    assert is_locked(business, now=now) is True


def test_trial_boundary_is_locked_exactly_at_the_end():
    business = _make_business()
    sub = get_subscription(business)

    assert is_locked(business, now=sub.trial_ends_at - timedelta(seconds=1)) is False
    assert is_locked(business, now=sub.trial_ends_at) is True


def test_active_subscription_running():
    business = _make_business()
    sub = get_subscription(business)
    base = sub.started_at
    Subscription.objects.filter(pk=sub.pk).update(
        status="active",
        trial_ends_at=None,
        current_period_start=base,
        current_period_end=base + timedelta(days=30),
        purge_at=None,
    )

    state = effective_state(business, now=base + timedelta(days=10))

    assert state == {
        "state": "active",
        "days_left": 20,
        "locked": False,
        "purge_at": None,
    }


def test_active_subscription_past_period_end_is_expired_and_locked():
    business = _make_business()
    sub = get_subscription(business)
    base = sub.started_at
    Subscription.objects.filter(pk=sub.pk).update(
        status="active",
        trial_ends_at=None,
        current_period_start=base,
        current_period_end=base + timedelta(days=30),
    )

    state = effective_state(business, now=base + timedelta(days=31))

    assert state["state"] == "expired"
    assert state["locked"] is True
    assert state["days_left"] == 0


def test_active_without_period_end_is_open_ended():
    business = _make_business()
    sub = get_subscription(business)
    Subscription.objects.filter(pk=sub.pk).update(
        status="active", trial_ends_at=None, current_period_end=None
    )

    state = effective_state(business, now=sub.started_at + timedelta(days=999))

    assert state["state"] == "active"
    assert state["days_left"] is None
    assert state["locked"] is False


@pytest.mark.parametrize("stored", ["locked", "expired"])
def test_stored_locked_or_expired_is_locked_even_with_future_dates(stored):
    business = _make_business()
    sub = get_subscription(business)
    Subscription.objects.filter(pk=sub.pk).update(status=stored)

    state = effective_state(business, now=sub.started_at + timedelta(days=1))

    assert state["state"] == stored
    assert state["locked"] is True
    assert state["days_left"] == 0


def test_business_without_a_subscription_is_not_locked():
    business = _make_business()
    Subscription.objects.filter(business=business).delete()

    assert effective_state(business) == {
        "state": "none",
        "days_left": 0,
        "locked": False,
        "purge_at": None,
    }
    assert is_locked(business) is False