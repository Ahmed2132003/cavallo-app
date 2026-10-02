"""
Part P-090: Paymob payment webhook.

POST /api/v1/payments/webhook/paymob/

SECURITY MODEL (architecture Section 28 "webhook spoofing", Section 5
rule 11): this endpoint is unauthenticated BY NECESSITY (the gateway
cannot carry a user's JWT), so its whole security rests on verifying the
gateway's signature before trusting anything in the payload.

* The signature is verified FIRST, on the raw request body, through the
  configured gateway (get_gateway(), never a concrete provider import).
  Failure -> HTTP 400 immediately; the payload is not parsed or used.
* Paymob delivers its HMAC in the ``hmac`` QUERY parameter (not a
  header); see payments/gateways/paymob.py (P-089).
* CSRF: DRF's APIView is csrf_exempt, and with no authentication classes
  there is no session auth that could re-enable the check.

Once verified and parsed, the event is handed to
payments.webhooks.process_webhook_event(): idempotency, atomic updates
and activation through monetization.services.activate_subscription().
Every handled outcome (activated, duplicate, ignored, unmatched, ...)
is HTTP 200 so the gateway does not retry; unexpected errors propagate
as 500 so it does.
"""

import json
import logging
from dataclasses import dataclass

from django.http import HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from payments.gateways import get_gateway
from payments.webhooks import process_webhook_event

logger = logging.getLogger(__name__)

SIGNATURE_QUERY_PARAM = "hmac"


@dataclass(frozen=True)
class WebhookEvent:
    """The fields of Paymob's Transaction Processed callback we use."""

    transaction_id: str
    order_id: str
    merchant_order_id: str
    success: bool
    pending: bool
    is_voided: bool
    is_refunded: bool
    amount_cents: int | None
    currency: str


def _is_true(value) -> bool:
    """Fail closed: only a real boolean True / the text "true" counts."""
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _parse_webhook_payload(raw_body: bytes) -> WebhookEvent | None:
    """
    Extract the event from an ALREADY signature-verified body.
    Returns None when the shape is unusable (no ``obj`` / no ``id``).
    """
    try:
        payload = json.loads(raw_body)
        obj = payload["obj"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(obj, dict) or obj.get("id") in (None, ""):
        return None

    order = obj.get("order")
    if not isinstance(order, dict):
        order = {}
    amount_cents = obj.get("amount_cents")
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
        amount_cents = None

    return WebhookEvent(
        transaction_id=str(obj["id"]),
        order_id="" if order.get("id") is None else str(order["id"]),
        merchant_order_id=str(order.get("merchant_order_id") or ""),
        success=_is_true(obj.get("success")),
        pending=_is_true(obj.get("pending")),
        is_voided=_is_true(obj.get("is_voided")),
        is_refunded=_is_true(obj.get("is_refunded")),
        amount_cents=amount_cents,
        currency=str(obj.get("currency") or ""),
    )


class PaymobWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request):
        # Raw bytes first; request.data is never touched on this path.
        raw_body = request.body
        signature = request.query_params.get(SIGNATURE_QUERY_PARAM, "")

        # 1) Signature gate. Nothing else runs before this.
        if not get_gateway().verify_webhook_signature(raw_body, signature):
            logger.warning("Payment webhook rejected: invalid signature")
            return HttpResponse(status=400)

        # 2) Verified. Only now do we look at the content.
        event = _parse_webhook_payload(raw_body)
        if event is None:
            logger.warning("Payment webhook rejected: unusable payload shape")
            return HttpResponse(status=400)

        # 3) Process (idempotent, atomic). See payments/webhooks.py.
        outcome = process_webhook_event(event)
        logger.info(
            "Payment webhook processed: transaction=%s outcome=%s",
            event.transaction_id,
            outcome,
        )
        return HttpResponse(status=200)
