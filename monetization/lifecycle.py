"""
BE-08: storage-subscription lifecycle (purchase rules, expiry, reminders).

apply_pending_if_due() / expire_if_due() only mutate the instance; the
caller holds the row lock and saves.
"""

import logging
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from monetization.constants import BYTES_PER_GB, PURGE_GRACE_DAYS
from monetization.models import StoredFile, Subscription, SubscriptionReminder
from monetization.notifications import notify_subscription_event
from monetization.services import custom_quota_bytes

logger = logging.getLogger(__name__)

STORAGE_PERIOD_DAYS = 30
PAID_PURGE_GRACE_DAYS = 60  # D5. Trials keep PURGE_GRACE_DAYS (BE-02).
REMINDER_DAYS = (1, 3)

DOWNGRADE_BLOCKED_USAGE = "downgrade_blocked_usage"
DOWNGRADE_ALREADY_PENDING = "downgrade_already_pending"


class PurchaseBlocked(Exception):
    def __init__(self, code, message, fields):
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = fields


def used_bytes(business_id):
    total = StoredFile.objects.filter(business_id=business_id).aggregate(
        total=Sum("size_bytes")
    )["total"]
    return total or 0


def target_quota_bytes(plan, gb):
    return custom_quota_bytes(gb) if plan.is_custom else plan.storage_bytes


def is_running(sub, now):
    """A paid period that has not ended yet."""
    return (
        sub.status == Subscription.STATUS_ACTIVE
        and sub.current_period_end is not None
        and sub.current_period_end > now
    )


def is_downgrade(sub, plan, quota):
    """Smaller than the current quota (renewing a fixed plan never counts)."""
    if plan.pk == sub.plan_id and not plan.is_custom:
        return False
    return quota < sub.quota_bytes


def check_purchase_allowed(business, plan, gb, now=None):
    """Called BEFORE payment. Raises PurchaseBlocked."""
    now = now or timezone.now()
    quota = target_quota_bytes(plan, gb)
    if quota is None:
        return
    sub = Subscription.objects.filter(business_id=business.pk).first()
    if sub is not None and not is_downgrade(sub, plan, quota):
        return
    used = used_bytes(business.pk)
    if used > quota:
        raise PurchaseBlocked(
            DOWNGRADE_BLOCKED_USAGE,
            "Your used storage is larger than this plan's storage.",
            {"used_bytes": used, "target_quota_bytes": quota},
        )
    # A second downgrade would overwrite one that is already paid for.
    if sub is not None and is_running(sub, now) and sub.pending_plan_id:
        raise PurchaseBlocked(
            DOWNGRADE_ALREADY_PENDING,
            "A plan change is already scheduled for your next period.",
            {},
        )


def apply_pending_if_due(sub, now):
    """Start the paid-for pending plan once the current period has ended."""
    end = sub.current_period_end
    if (
        sub.status != Subscription.STATUS_ACTIVE
        or sub.pending_plan_id is None
        or end is None
        or end > now
    ):
        return False
    plan = sub.pending_plan
    quota = sub.pending_quota_bytes
    if quota is None:
        quota = plan.storage_bytes or sub.quota_bytes
    sub.plan = plan
    sub.quota_bytes = quota
    sub.custom_gb = quota // BYTES_PER_GB if plan.is_custom else None
    sub.is_featured_effective = plan.is_featured
    sub.current_period_start = end
    sub.current_period_end = end + timedelta(days=STORAGE_PERIOD_DAYS)
    sub.pending_plan = None
    sub.pending_quota_bytes = None
    sub.purge_at = None
    return True


def expire_if_due(sub, now):
    """trialing -> locked, active -> expired, once the end date has passed."""
    if sub.status == Subscription.STATUS_TRIALING:
        end = sub.trial_ends_at
        if end is None or end > now:
            return False
        sub.status = Subscription.STATUS_LOCKED
        sub.purge_at = sub.purge_at or end + timedelta(days=PURGE_GRACE_DAYS)
    elif sub.status == Subscription.STATUS_ACTIVE:
        end = sub.current_period_end
        if end is None or end > now:
            return False
        sub.status = Subscription.STATUS_EXPIRED
        sub.purge_at = end + timedelta(days=PAID_PURGE_GRACE_DAYS)
    else:
        return False
    sub.is_featured_effective = False
    return True


def drop_profile_cache(business_id):
    """Drop the cached public profile after commit."""

    def _drop():
        from businesses.views import _business_profile_cache_key

        cache.delete(_business_profile_cache_key(business_id))

    transaction.on_commit(_drop)


def expire_storage_subscriptions(now=None):
    now = now or timezone.now()
    counts = {"renewed": 0, "expired": 0, "locked": 0}
    due = Q(status=Subscription.STATUS_TRIALING, trial_ends_at__lte=now) | Q(
        status=Subscription.STATUS_ACTIVE, current_period_end__lte=now
    )
    ids = list(Subscription.objects.filter(due).values_list("pk", flat=True))
    for pk in ids:
        with transaction.atomic():
            # Re-checked under the lock: a payment may have just landed.
            sub = Subscription.objects.select_for_update().filter(pk=pk).first()
            if sub is None:
                continue
            renewed = apply_pending_if_due(sub, now)
            ended = expire_if_due(sub, now)
            if not (renewed or ended):
                continue
            sub.save()
            drop_profile_cache(sub.business_id)
        counts["renewed"] += int(renewed)
        if ended:
            counts[sub.status] += 1
            # Runs once per ended period: the status change above was
            # committed under the row lock, so a re-run finds nothing.
            event = (
                "trial_expired"
                if sub.status == Subscription.STATUS_LOCKED
                else "subscription_expired"
            )
            try:
                notify_subscription_event(sub.business, event)
            except Exception:  # a notification failure must not stop the sweep
                logger.exception(
                    "notify_subscription_event failed: business=%s event=%s",
                    sub.business_id,
                    event,
                )
    return counts


def send_due_reminders(now=None):
    now = now or timezone.now()
    horizon = now + timedelta(days=max(REMINDER_DAYS))
    trial = Q(
        status=Subscription.STATUS_TRIALING,
        trial_ends_at__gt=now,
        trial_ends_at__lte=horizon,
    )
    # A pending plan means the next period is already paid: no reminder.
    paid = Q(
        status=Subscription.STATUS_ACTIVE,
        pending_plan__isnull=True,
        current_period_end__gt=now,
        current_period_end__lte=horizon,
    )
    sent = 0
    for sub in Subscription.objects.filter(trial | paid).select_related("business"):
        if sub.status == Subscription.STATUS_TRIALING:
            prefix, end = "trial", sub.trial_ends_at
        else:
            prefix, end = "subscription", sub.current_period_end
        days = next(d for d in REMINDER_DAYS if end - now <= timedelta(days=d))
        event = f"{prefix}_ends_{days}d"
        with transaction.atomic():
            _, created = SubscriptionReminder.objects.get_or_create(
                subscription=sub, event=event, period_end=end
            )
            if created:
                notify_subscription_event(sub.business, event)
                sent += 1
    return sent