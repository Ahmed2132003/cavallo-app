"""BE-07: storage-plan payment quoting and initiation."""

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction as db_transaction
from rest_framework.exceptions import ValidationError

from monetization.models import Plan
from monetization.services import (
    calculate_custom_price,
    custom_quota_bytes,
    parse_gb,
)
from payments.gateways import get_gateway
from payments.gateways.base import PaymentGateway, PaymentGatewayError
from payments.models import (
    KIND_STORAGE,
    STATUS_PENDING,
    Subscription,
    Transaction,
)
from payments.services import _mark_failed, _start_at_gateway

STORAGE_CURRENCY = "EGP"
_TWO_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class StorageQuote:
    plan: Plan
    gb: Decimal | None
    amount: Decimal
    quota_bytes: int


def quote_storage_plan(plan_code, gb=None) -> StorageQuote:
    """Resolve a storage plan and calculate its server-side price."""
    plan = Plan.objects.filter(code=plan_code, is_active=True).first()
    if plan is None or plan.code == "free":
        raise ValidationError({"plan": "Unknown or unavailable plan."})

    if plan.is_custom:
        if gb is None:
            raise ValidationError({"gb": "gb is required for the custom plan."})

        gb_value = parse_gb(gb)
        return StorageQuote(
            plan=plan,
            gb=gb_value,
            amount=calculate_custom_price(gb_value),
            quota_bytes=custom_quota_bytes(gb_value),
        )

    if gb is not None:
        raise ValidationError({"gb": "gb is only accepted for the custom plan."})

    if plan.price_egp is None or plan.price_egp <= 0 or plan.storage_bytes is None:
        raise ValidationError({"plan": "This plan cannot be purchased."})

    return StorageQuote(
        plan=plan,
        gb=None,
        amount=plan.price_egp.quantize(_TWO_PLACES),
        quota_bytes=plan.storage_bytes,
    )


def initiate_storage_payment(
    business, quote: StorageQuote, gateway: PaymentGateway | None = None
):
    """
    Create a pending storage payment and start it at the configured gateway.
    Returns (payments.Subscription, payment_url). Never activates a plan.
    """
    if gateway is None:
        gateway = get_gateway()

    with db_transaction.atomic():
        subscription = Subscription.objects.create(
            business=business,
            plan=quote.plan,
            kind=KIND_STORAGE,
            gb=quote.gb,
            status=STATUS_PENDING,
        )
        txn = Transaction.objects.create(
            subscription=subscription,
            transaction_id=None,
            amount=quote.amount,
            currency=STORAGE_CURRENCY,
            status=STATUS_PENDING,
        )

    payment_url = _start_at_gateway(gateway, subscription, txn)
    return subscription, payment_url