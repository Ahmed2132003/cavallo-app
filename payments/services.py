"""
Part P-089 (STEP 3): payment-initiation service.

``initiate_subscription_payment()`` is what the future Web Dashboard
(P-092) calls. It talks to the gateway ONLY through the PaymentGateway
interface (payments.gateways), never to a concrete provider.

NAMING: this creates a ``payments.Subscription`` (the PAYMENT record).
It never creates or activates a ``monetization.FeaturedSubscription``;
that happens only in P-090, after a verified webhook, through
``monetization.services.activate_subscription()``.
"""

import logging

from django.db import transaction as db_transaction

from payments.gateways import get_gateway
from payments.gateways.base import PaymentGateway, PaymentGatewayError
from payments.models import (
    STATUS_FAILED,
    STATUS_PENDING,
    Subscription,
    Transaction,
)

logger = logging.getLogger(__name__)


def _mark_failed(subscription: Subscription, txn: Transaction) -> None:
    subscription.status = STATUS_FAILED
    subscription.save(update_fields=["status", "updated_at"])
    txn.status = STATUS_FAILED
    txn.save(update_fields=["status", "updated_at"])


def initiate_subscription_payment(
    business, plan, gateway: PaymentGateway | None = None
) -> str:
    """
    Start a payment for ``plan`` on behalf of ``business`` and return the
    gateway-hosted payment URL to redirect the user to.

    Creates a pending Subscription and a pending Transaction (amount and
    currency are a snapshot of the plan at purchase time; the gateway's
    transaction id is unknown until the webhook, so it starts as NULL).
    The gateway call happens AFTER those rows are committed and outside any
    DB transaction. If it fails, both rows are marked ``failed`` (kept for
    audit) and PaymentGatewayError is re-raised.

    ``gateway`` can be injected (tests); by default the configured one is
    used (settings.PAYMENT_GATEWAY).
    """
    if gateway is None:
        gateway = get_gateway()

    with db_transaction.atomic():
        subscription = Subscription.objects.create(
            business=business, plan=plan, status=STATUS_PENDING
        )
        txn = Transaction.objects.create(
            subscription=subscription,
            transaction_id=None,
            amount=plan.price,
            currency=plan.currency,
            status=STATUS_PENDING,
        )

    try:
        result = gateway.initiate_payment(subscription)
        payment_url = result["payment_url"]
        gateway_reference = result["gateway_reference"]
        if not payment_url or not gateway_reference:
            raise KeyError("empty payment_url / gateway_reference")
    except (KeyError, TypeError) as exc:
        _mark_failed(subscription, txn)
        raise PaymentGatewayError(
            "Gateway returned an invalid initiate_payment result"
        ) from exc
    except PaymentGatewayError:
        logger.warning(
            "Payment initiation failed for payments.Subscription %s", subscription.pk
        )
        _mark_failed(subscription, txn)
        raise

    subscription.gateway_reference = str(gateway_reference)
    subscription.save(update_fields=["gateway_reference", "updated_at"])
    return payment_url
