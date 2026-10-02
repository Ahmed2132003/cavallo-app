"""
Part P-092 (STEP 2): payment initiation for the Web Dashboard.

POST /api/v1/payments/initiate/   body: {"plan_id": <int>}
-> 201 {"payment_url": "<gateway-hosted checkout page>"}

This is a THIN API surface over payments.services.
initiate_subscription_payment() (P-089); no payment logic lives here.

* Authenticated (JWT, same login as the mobile app) and Business accounts
  only. A Customer account gets 403; a Business account without a
  BusinessProfile yet gets 404 (same message style as businesses/me/).
* IDOR: the buyer is ALWAYS ``request.user.business_profile`` (the P-026
  /me/ pattern). No business id is ever read from the URL or the body.
* This view never activates Featured status. Activation happens only
  after a verified webhook (P-090) or the reconciliation job (P-091), via
  monetization.services.activate_subscription(). So this module must not
  touch FeaturedSubscription / is_featured / is_active (guarded by a test).
* Gateway failures (including missing credentials) surface as HTTP 503
  with code SERVICE_UNAVAILABLE and a generic message; the gateway's own
  error text is logged, never returned to the client.

Why this is not in payments/views.py: that module is the webhook view and
P-090's guard test forbids any monetization import there.
"""

import logging

from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from businesses.models import BusinessProfile
from payments.gateways.base import PaymentGatewayError
from payments.serializers import PaymentInitiateSerializer
from payments.services import initiate_subscription_payment

logger = logging.getLogger(__name__)


class PaymentServiceUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The payment service is temporarily unavailable. Try again later."
    default_code = "service_unavailable"


class PaymentInitiateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        if user.account_type != User.ACCOUNT_TYPE_BUSINESS:
            raise PermissionDenied("Only business accounts can purchase a plan.")

        try:
            business = user.business_profile
        except BusinessProfile.DoesNotExist:
            raise NotFound(
                "You don't have a business profile yet. "
                "Create one before purchasing a plan."
            )

        serializer = PaymentInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = serializer.validated_data["plan"]

        try:
            payment_url = initiate_subscription_payment(business, plan)
        except PaymentGatewayError:
            logger.error(
                "Payment initiation failed: business=%s plan=%s",
                business.pk,
                plan.pk,
                exc_info=True,
            )
            raise PaymentServiceUnavailable()

        return Response({"payment_url": payment_url}, status=status.HTTP_201_CREATED)
