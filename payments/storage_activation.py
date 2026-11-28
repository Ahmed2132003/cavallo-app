"""
BE-07 / BE-08: storage-plan activation.

Called by payments.webhooks ONLY after a verified, amount-matched,
first-time success of a payment whose kind is "storage".

Deliberately NOT named ``activate_subscription``: that name already
belongs to monetization.services (the Featured path).

Idempotency is NOT handled here: the webhook guarantees one call per
payment (completed-transaction check + completed-subscription check,
inside the row lock and one transaction.atomic()).
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from monetization.lifecycle import (
    STORAGE_PERIOD_DAYS,
    apply_pending_if_due,
    drop_profile_cache,
    is_downgrade,
    is_running,
    target_quota_bytes,
)
from monetization.models import Subscription as StorageSubscription
from monetization.notifications import notify_subscription_event

logger = logging.getLogger(__name__)


def activate_storage_subscription(business, plan, gb, payment):
    """
    Apply a paid storage purchase to the business's single Subscription.

    * Renewal of a running plan: the period is extended from its end.
    * Smaller plan while a period is running: stored as pending_plan and
      started when that period ends (D9).
    * Anything else (upgrade, trial, expired, first purchase): immediate,
      new period starting now (D8).
    Always clears purge_at. Stored files / used bytes are never touched.
    """
    now = timezone.now()
    period = timedelta(days=STORAGE_PERIOD_DAYS)
    quota = target_quota_bytes(plan, gb)
    with transaction.atomic():
        sub = (
            StorageSubscription.objects.select_for_update()
            .filter(business_id=business.pk)
            .first()
        )
        if sub is None:
            sub = StorageSubscription(business_id=business.pk, started_at=now)
            running = False
        else:
            apply_pending_if_due(sub, now)
            running = is_running(sub, now)

        if running and is_downgrade(sub, plan, quota):
            if sub.pending_plan_id:
                logger.warning(
                    "Pending plan overwritten: business=%s payment=%s",
                    business.pk,
                    payment.pk,
                )
            sub.pending_plan = plan
            sub.pending_quota_bytes = quota
        else:
            renewal = (
                running
                and sub.plan_id == plan.pk
                and (not plan.is_custom or sub.quota_bytes == quota)
            )
            if renewal:
                sub.current_period_end += period
            else:
                if sub.pending_plan_id:
                    logger.warning(
                        "Paid pending plan discarded: business=%s payment=%s",
                        business.pk,
                        payment.pk,
                    )
                sub.current_period_start = now
                sub.current_period_end = now + period
                sub.pending_plan = None
                sub.pending_quota_bytes = None
            sub.plan = plan
            sub.quota_bytes = quota
            sub.custom_gb = int(gb) if gb is not None else None
            sub.is_featured_effective = plan.is_featured

        sub.status = StorageSubscription.STATUS_ACTIVE
        sub.purge_at = None
        sub.canceled_at = None
        sub.save()
        drop_profile_cache(business.pk)
        # BE-11: scheduled on commit (the webhook's outer transaction too).
        # A notification problem must never roll back a paid activation.
        try:
            notify_subscription_event(business, "subscription_activated")
        except Exception:
            logger.exception(
                "subscription_activated notification failed: business=%s", business.pk
            )
    logger.info(
        "Storage subscription activated: business=%s plan=%s payment=%s",
        business.pk,
        plan.pk,
        payment.pk,
    )
    return sub
    """
    Simple activation: status=active, a 30-day period starting now, the
    plan's quota (or the paid-for custom quota), purge_at cleared.
    Creates the business's Subscription row when it has none, otherwise
    updates the single existing row in place.
    """
    now = timezone.now()
    quota_bytes = custom_quota_bytes(gb) if plan.is_custom else plan.storage_bytes
    fields = {
        "plan": plan,
        "status": StorageSubscription.STATUS_ACTIVE,
        "current_period_start": now,
        "current_period_end": now + timedelta(days=STORAGE_PERIOD_DAYS),
        "quota_bytes": quota_bytes,
        "custom_gb": int(gb) if gb is not None else None,
        "purge_at": None,
        "pending_plan": None,
        "canceled_at": None,
    }
    with transaction.atomic():
        sub = (
            StorageSubscription.objects.select_for_update()
            .filter(business_id=business.pk)
            .first()
        )
        if sub is None:
            sub = StorageSubscription.objects.create(
                business_id=business.pk, started_at=now, **fields
            )
        else:
            for name, value in fields.items():
                setattr(sub, name, value)
            sub.save()
    logger.info(
        "Storage subscription activated: business=%s plan=%s payment=%s",
        business.pk,
        plan.pk,
        payment.pk,
    )
    return sub