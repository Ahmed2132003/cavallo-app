"""
BE-07: storage-plan checkout over Paymob (via the PaymentGateway interface).

POST /api/v1/payments/subscriptions/checkout/
     body {"plan": "<plan code>", "gb": <number, custom plan only>}
  -> 201 {"payment_id", "checkout_url", "amount_egp"}
GET  /api/v1/payments/subscriptions/checkout/<payment_id>/status/
  -> 200 {"payment_id", "status", "plan", "gb", "amount_egp"}

* Business accounts only (IsBusinessAccount). Deliberately NOT gated by
  HasActiveSubscription: a locked merchant must be able to pay to renew.
* The amount is computed on the server only (quote_storage_plan).
* The payer / owner is always request.user's own business profile; a
  payment of another business (or a Featured payment) is a 404.
* Nothing here activates a plan: activation happens only in the verified
  webhook (payments.webhooks -> payments.storage_activation).
"""

import logging

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from monetization.permissions import IsBusinessAccount
from payments.gateways.base import PaymentGatewayError
from payments.initiate_views import PaymentServiceUnavailable
from payments.models import KIND_STORAGE, Subscription
from payments.serializers import SubscriptionCheckoutSerializer
from payments.storage_services import (
    initiate_storage_payment,
    quote_storage_plan,
)

logger = logging.getLogger(__name__)


def _own_business(user):
    business = getattr(user, "business_profile", None)
    if business is None:
        raise NotFound(
            "You don't have a business profile yet. "
            "Create one before purchasing a plan."
        )
    return business


class SubscriptionCheckoutView(APIView):
    permission_classes = [IsBusinessAccount]

    def post(self, request):
        business = _own_business(request.user)

        serializer = SubscriptionCheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        quote = quote_storage_plan(
            serializer.validated_data["plan"],
            serializer.validated_data.get("gb"),
        )

        try:
            payment, checkout_url = initiate_storage_payment(business, quote)
        except PaymentGatewayError:
            logger.error(
                "Storage checkout failed: business=%s plan=%s",
                business.pk,
                quote.plan.pk,
                exc_info=True,
            )
            raise PaymentServiceUnavailable()

        return Response(
            {
                "payment_id": payment.pk,
                "checkout_url": checkout_url,
                "amount_egp": str(quote.amount),
            },
            status=status.HTTP_201_CREATED,
        )


class SubscriptionCheckoutStatusView(APIView):
    permission_classes = [IsBusinessAccount]

    def get(self, request, payment_id):
        business = _own_business(request.user)
        payment = (
            Subscription.objects.select_related("plan")
            .filter(pk=payment_id, business=business, kind=KIND_STORAGE)
            .first()
        )
        if payment is None:
            raise NotFound("Payment not found.")

        amount, _currency = payment.charge_amount()
        return Response(
            {
                "payment_id": payment.pk,
                "status": payment.status,
                "plan": payment.plan.code,
                "gb": str(payment.gb) if payment.gb is not None else None,
                "amount_egp": str(amount),
            }
        )