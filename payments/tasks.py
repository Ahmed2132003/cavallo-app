"""
Daily payment reconciliation - Celery Beat job (Part P-091).

WHY: webhooks (P-090) are best-effort. If Paymob's callback never reaches
us (network issue, downtime, misconfiguration) a payment that genuinely
succeeded stays ``pending`` locally forever and the business never becomes
Featured. This job is the safety net (architecture Section 22): it asks
the gateway what actually happened to every payment that has been pending
"too long".

NOT a replacement for the webhook: no real-time trigger, one daily batch.

SINGLE IMPLEMENTATION OF ACTIVATION: a gateway answer found here is turned
into the SAME ``WebhookEvent`` the webhook view builds and handed to the
SAME ``payments.webhooks.process_webhook_event()``. That function owns the
idempotency check, row locking, amount/currency check, status updates and
the call to ``monetization.services.activate_subscription()`` (the only
sanctioned activation path). Nothing in this module activates anything or
writes a status itself, so the two paths cannot drift apart.

GRACE PERIOD: only transactions pending for MORE than
RECONCILIATION_GRACE_PERIOD (1 hour) are looked at, so a webhook that is
merely slow is never second-guessed. One hour is far above normal webhook
latency (seconds) and far below the daily cadence.

LOOKUP: a locally-pending Transaction has no gateway transaction id yet
(the webhook fills it in, and the webhook is what went missing), so the
gateway is asked about the payments.Subscription (its gateway reference /
merchant reference), once per subscription.

OUTCOMES per subscription:
* gateway says completed / failed -> process_webhook_event() (idempotent).
* gateway says still pending (or voided/refunded) -> left completely alone.
* gateway has no transaction (customer never paid) -> left alone.
* gateway call fails (PaymentGatewayError) -> logged, left alone, retried
  by tomorrow's run. One bad payment never stops the batch.

IDEMPOTENT (Architecture Section 5, rule 8): processed transactions leave
``pending``, so they are not selected again; and if a webhook and this job
race, process_webhook_event() reports a duplicate instead of activating
twice.

Known limitation (flagged, not built): payments that were abandoned and
will never complete stay pending, so they are asked about again every day.
A maximum age could be added later if the volume ever matters.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from payments.gateways import get_gateway
from payments.gateways.base import GATEWAY_STATUS_PENDING, PaymentGatewayError
from payments.models import STATUS_PENDING, Transaction
from payments.views import WebhookEvent
from payments.webhooks import process_webhook_event

logger = logging.getLogger(__name__)

# See the module docstring (GRACE PERIOD).
RECONCILIATION_GRACE_PERIOD = timedelta(hours=1)


def _to_event(result) -> WebhookEvent:
    """Gateway answer -> the same event type the webhook produces."""
    return WebhookEvent(
        transaction_id=result.transaction_id,
        order_id=result.order_id,
        merchant_order_id=result.merchant_order_id,
        success=result.success,
        pending=result.pending,
        is_voided=result.is_voided,
        is_refunded=result.is_refunded,
        amount_cents=result.amount_cents,
        currency=result.currency,
    )


def _pending_subscriptions(cutoff):
    """
    Distinct payments.Subscription rows that still have a pending
    Transaction created at or before ``cutoff``, oldest first. A
    subscription is asked about once even if it has several such rows.
    """
    rows = (
        Transaction.objects.filter(
            status=STATUS_PENDING,
            created_at__lte=cutoff,
            subscription__status=STATUS_PENDING,
        )
        .select_related("subscription")
        .order_by("created_at", "pk")
    )
    seen = set()
    subscriptions = []
    for txn in rows:
        if txn.subscription_id not in seen:
            seen.add(txn.subscription_id)
            subscriptions.append(txn.subscription)
    return subscriptions


@shared_task(name="payments.reconcile_pending_transactions", ignore_result=True)
def reconcile_pending_transactions():
    """
    Reconcile every payment stuck pending past the grace period against the
    gateway. Returns counters so callers/tests can assert on a run:

        checked   subscriptions the gateway was asked about
        processed completed/failed answers handed to process_webhook_event
        skipped   still pending at the gateway, or no gateway transaction
        errors    gateway/processing failures (left for the next run)
    """
    cutoff = timezone.now() - RECONCILIATION_GRACE_PERIOD
    gateway = get_gateway()
    counts = {"checked": 0, "processed": 0, "skipped": 0, "errors": 0}

    for subscription in _pending_subscriptions(cutoff):
        counts["checked"] += 1
        try:
            result = gateway.check_transaction_status(subscription)
        except PaymentGatewayError:
            logger.exception(
                "Reconciliation: gateway check failed for subscription %s",
                subscription.pk,
            )
            counts["errors"] += 1
            continue

        if result is None or result.status == GATEWAY_STATUS_PENDING:
            logger.info(
                "Reconciliation: subscription %s left alone (gateway: %s)",
                subscription.pk,
                "no transaction" if result is None else "still pending",
            )
            counts["skipped"] += 1
            continue

        try:
            outcome = process_webhook_event(_to_event(result))
        except Exception:
            logger.exception(
                "Reconciliation: processing failed for subscription %s",
                subscription.pk,
            )
            counts["errors"] += 1
            continue

        logger.info(
            "Reconciliation: subscription %s transaction=%s outcome=%s",
            subscription.pk,
            result.transaction_id,
            outcome,
        )
        counts["processed"] += 1

    logger.info("Reconciliation finished: %s", counts)
    return counts
