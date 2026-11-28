"""BE-06 test helpers: put a business into a given subscription state."""

from datetime import timedelta

from django.utils import timezone

from products.models import Product
from monetization.models import Plan, Subscription


def free_plan():
    plan = Plan.objects.filter(code="free").first()
    if plan is None:
        plan = Plan.objects.create(
            name="Free",
            code="free",
            duration_days=1,
            price=0,
            currency=Product.CURRENCY_CHOICES[0][0],
        )
    return plan


def give_subscription(business, status, trial_days=None, period_days=None):
    """trial_days / period_days: offset from now (negative = already past)."""
    now = timezone.now()
    defaults = {
        "plan": free_plan(),
        "status": status,
        "started_at": now - timedelta(days=30),
        "trial_ends_at": None if trial_days is None else now + timedelta(days=trial_days),
        "current_period_end": None if period_days is None else now + timedelta(days=period_days),
        "quota_bytes": 1024,
    }
    sub, _ = Subscription.objects.update_or_create(business=business, defaults=defaults)
    return sub


# (id, status, trial_days, period_days, expect_locked)
STATE_CASES = [
    ("trial-running", Subscription.STATUS_TRIALING, 5, None, False),
    ("trial-expired", Subscription.STATUS_TRIALING, -1, None, True),
    ("paid-active", Subscription.STATUS_ACTIVE, None, 10, False),
    ("paid-expired", Subscription.STATUS_ACTIVE, None, -1, True),
    ("stored-expired", Subscription.STATUS_EXPIRED, None, None, True),
    ("stored-locked", Subscription.STATUS_LOCKED, None, None, True),
]