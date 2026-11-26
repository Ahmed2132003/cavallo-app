"""
BE-07: TEMPORARY storage-plan activation hook.

Called by payments.webhooks ONLY after a verified, amount-matched,
first-time success of a payment whose kind is "storage". BE-08 replaces
the body (proration, pending_plan, featured effect, ...) and keeps this
signature, so the webhook does not change.

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

from monetization.models import Subscription as StorageSubscription
from monetization.services import custom_quota_bytes

logger = logging.getLogger(__name__)

STORAGE_PERIOD_DAYS = 30


def activate_storage_subscription(business, plan, gb, payment):
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