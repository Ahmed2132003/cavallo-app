"""
Featured-subscription expiry sweep - Celery Beat job (Part P-088).

Completes the Featured lifecycle started by P-086 (activate) and P-087
(BusinessProfile.is_featured mirrors subscription state): once a
FeaturedSubscription passes expires_at, it must stop boosting its
business. Same "do not let expired state linger" discipline as the Story
expiry sweep (P-048), applied to a different domain.

This task NEVER writes FeaturedSubscription.is_active or
BusinessProfile.is_featured itself. It selects the expired-but-still-
active rows and hands that queryset to
monetization.services.deactivate_subscriptions(), which, in ONE
transaction: locks the affected business rows (same lock as
activate_subscription), deactivates the rows (kept for history), and
RECOMPUTES is_featured from the subscription table. A business is
therefore only un-featured when it has no other active subscription
left. That recompute is the defensive guard against an overlapping
subscription: the DB constraint uniq_active_featured_per_biz already
forbids two active rows per business, and even if that invariant were
ever broken by a future bug, the flag would still be derived from the
table instead of being blindly set to False.

Idempotent (Architecture Section 5, rule 8): the queryset only matches
rows that are still is_active=True, so a re-run (or an overlapping run)
finds nothing and deactivates 0 rows. Expected volume is low (one row
per purchase), so no index on (is_active, expires_at) was added.

Cadence: DAILY (00:30 UTC, see CELERY_BEAT_SCHEDULE). Featured status
does not need the second-level precision Story expiry needed. Worst
case, a subscription stays boosted for up to ~24h past expires_at.

Out of scope (flagged, not built): notifying the business that its
Featured status expired. Phase 13's notification infrastructure could
support it as a future enhancement.
"""

import logging

from celery import shared_task
from django.utils import timezone

from monetization.models import FeaturedSubscription
from monetization.services import deactivate_subscriptions

logger = logging.getLogger(__name__)


@shared_task(name="monetization.expire_featured_subscriptions", ignore_result=True)
def expire_featured_subscriptions():
    """
    Deactivate every FeaturedSubscription that is still active but whose
    expires_at is in the past, and un-feature the affected businesses.

    Returns {"deactivated": <int>} (the number of subscriptions actually
    deactivated by this run) so callers/tests can assert on it.
    """
    now = timezone.now()
    expired = FeaturedSubscription.objects.filter(
        is_active=True, expires_at__lte=now
    )
    deactivated = deactivate_subscriptions(expired)
    logger.info(
        "expire_featured_subscriptions finished",
        extra={"deactivated": deactivated},
    )
    return {"deactivated": deactivated}
