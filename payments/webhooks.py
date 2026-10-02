"""
Part P-090 (STEP 2): processing of a signature-VERIFIED payment webhook.

Only called by payments.views.PaymobWebhookView AFTER the signature has
been verified. It never sees an unverified payload.

Rules:
* Activation goes ONLY through monetization.services.activate_subscription()
  (the sanctioned P-086 path). Nothing here creates a FeaturedSubscription
  or touches BusinessProfile.is_featured.
* IDEMPOTENCY: a Transaction with this transaction_id that is already
  ``completed`` means a PRIOR webhook processed it -> safe no-op. A
  ``pending`` row is NOT a duplicate: P-089 creates it (transaction_id
  NULL) before any webhook exists, and the first webhook must process it.
* CONCURRENCY: the payments.Subscription row is locked, then the
  duplicate check runs inside the lock, so two simultaneous deliveries of
  the same event cannot both activate.
* Everything that writes runs in ONE transaction.atomic(). If activation
  raises, all of it rolls back and the error propagates (HTTP 500, so the
  gateway retries).
* This module does not import a concrete gateway.
"""

import logging
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from django.db import transaction as db_transaction

from monetization.services import activate_subscription
from payments.models import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    Subscription,
    Transaction,
)

if TYPE_CHECKING:
    from payments.views import WebhookEvent

logger = logging.getLogger(__name__)

OUTCOME_ACTIVATED = "activated"
OUTCOME_DUPLICATE = "duplicate"
OUTCOME_FAILURE_RECORDED = "failure_recorded"
OUTCOME_IGNORED = "ignored"
OUTCOME_UNMATCHED = "unmatched"
OUTCOME_REJECTED = "rejected_mismatch"

# special_reference set by PaymobGateway.build_intention_body().
_MERCHANT_REF = re.compile(r"payments-sub-(\d+)")


def _to_cents(amount) -> int:
    cents = (Decimal(str(amount)) * 100).quantize(Decimal("1"), ROUND_HALF_UP)
    return int(cents)


def _amount_matches(event: "WebhookEvent", txn: Transaction) -> bool:
    if event.amount_cents is None:
        return False
    return (
        event.amount_cents == _to_cents(txn.amount)
        and event.currency.upper() == txn.currency.upper()
    )


def _resolve_subscription_id(event: "WebhookEvent"):
    """Find the payments.Subscription this event belongs to (or None)."""
    by_txn = (
        Transaction.objects.filter(transaction_id=event.transaction_id)
        .values_list("subscription_id", flat=True)
        .first()
    )
    if by_txn is not None:
        return by_txn

    if event.order_id:
        by_reference = (
            Subscription.objects.filter(gateway_reference=event.order_id)
            .order_by("-pk")
            .values_list("pk", flat=True)
            .first()
        )
        if by_reference is not None:
            return by_reference

    match = _MERCHANT_REF.fullmatch(event.merchant_order_id)
    if match:
        return (
            Subscription.objects.filter(pk=int(match.group(1)))
            .values_list("pk", flat=True)
            .first()
        )
    return None


def process_webhook_event(event: "WebhookEvent") -> str:
    """
    Apply a verified event. Returns one OUTCOME_* constant; every outcome
    maps to HTTP 200 in the view. Unexpected exceptions propagate.
    """
    # Not a final result: nothing to record yet.
    if event.pending or event.is_voided or event.is_refunded:
        logger.info(
            "Payment webhook ignored (pending/voided/refunded): transaction=%s",
            event.transaction_id,
        )
        return OUTCOME_IGNORED

    with db_transaction.atomic():
        subscription_id = _resolve_subscription_id(event)
        if subscription_id is None:
            log = logger.error if event.success else logger.warning
            log(
                "Payment webhook matches no payments.Subscription: "
                "transaction=%s order=%s merchant_order=%s success=%s",
                event.transaction_id,
                event.order_id,
                event.merchant_order_id,
                event.success,
            )
            return OUTCOME_UNMATCHED

        # Serialise concurrent deliveries for this payment.
        subscription = (
            Subscription.objects.select_for_update(of=("self",))
            .select_related("plan")
            .get(pk=subscription_id)
        )

        txn = (
            Transaction.objects.select_for_update()
            .filter(transaction_id=event.transaction_id)
            .first()
        )

        # Genuine duplicate: a PRIOR webhook already completed it.
        if txn is not None and txn.status == STATUS_COMPLETED:
            logger.info(
                "Payment webhook duplicate ignored: transaction=%s",
                event.transaction_id,
            )
            return OUTCOME_DUPLICATE

        if txn is not None and txn.subscription_id != subscription.pk:
            logger.error(
                "Payment webhook transaction %s belongs to another "
                "subscription than the one resolved (%s)",
                event.transaction_id,
                subscription.pk,
            )
            return OUTCOME_UNMATCHED

        if txn is None:
            # First delivery: take the pending row P-089 created.
            txn = (
                Transaction.objects.select_for_update()
                .filter(
                    subscription=subscription,
                    transaction_id__isnull=True,
                    status=STATUS_PENDING,
                )
                .order_by("pk")
                .first()
            )

        if txn is None:
            # A further attempt on the same payment (e.g. retry after a
            # failed card): new row, amount/currency from the latest one.
            template = subscription.transactions.order_by("-pk").first()
            if template is None:
                logger.error(
                    "Payment webhook: subscription %s has no transaction rows",
                    subscription.pk,
                )
                return OUTCOME_UNMATCHED
            txn = Transaction(
                subscription=subscription,
                transaction_id=None,
                amount=template.amount,
                currency=template.currency,
            )

        if event.success:
            if not _amount_matches(event, txn):
                logger.error(
                    "Payment webhook amount/currency mismatch, NOT activating: "
                    "transaction=%s event=%s %s expected=%s %s",
                    event.transaction_id,
                    event.amount_cents,
                    event.currency,
                    txn.amount,
                    txn.currency,
                )
                return OUTCOME_REJECTED

            if subscription.status == STATUS_COMPLETED:
                # Already activated through another transaction; never
                # activate twice for one payment record.
                logger.error(
                    "Payment webhook success for already-completed "
                    "subscription %s: transaction=%s (possible double payment)",
                    subscription.pk,
                    event.transaction_id,
                )
                return OUTCOME_IGNORED

            txn.transaction_id = event.transaction_id
            txn.status = STATUS_COMPLETED
            txn.save()
            subscription.status = STATUS_COMPLETED
            subscription.save(update_fields=["status", "updated_at"])

            activate_subscription(
                business=subscription.business, plan=subscription.plan
            )
            return OUTCOME_ACTIVATED

        # Final failure.
        txn.transaction_id = event.transaction_id
        txn.status = STATUS_FAILED
        txn.save()
        if subscription.status == STATUS_PENDING:
            subscription.status = STATUS_FAILED
            subscription.save(update_fields=["status", "updated_at"])
        return OUTCOME_FAILURE_RECORDED
