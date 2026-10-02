"""
Part P-089 (STEP 2): Paymob implementation of PaymentGateway.

Built against Paymob's current "Intention" (unified checkout) API:

* Create Intention:  POST {base}/v1/intention/
  https://developers.paymob.com/paymob-docs/developers/intention-apis/create-intention
  Auth header ``Authorization: Token <secret key>``. Body: amount (in
  cents), currency, payment_methods (integration ids), items, billing_data
  (first_name, last_name, email, phone_number are mandatory),
  special_reference, notification_url, redirection_url.
  Response (201): id, intention_order_id, client_secret, ...
* Unified checkout redirect:
  https://developers.paymob.com/paymob-docs/developers/checkout-experiences/unified-checkout-redirection
  ``{checkout}?publicKey=<public key>&clientSecret=<client secret>``
* Webhook HMAC (Transaction Processed callback):
  https://developers.paymob.com/paymob-docs/developers/webhook-callbacks-and-hmac/hmac/hmac-transaction-callback
  HMAC-SHA512 (hex, lowercase) over the concatenated VALUES of 20 fields
  in a fixed order, keyed with the dashboard HMAC secret, delivered in the
  ``hmac`` QUERY parameter (not a header, not the raw body).

NOT yet verified against a live Paymob account (no credentials, Section 7
item 3): everything here follows the documentation + mocked HTTP only.
"""

import hashlib
import hmac
import json
import logging
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlencode

import requests
from django.conf import settings

from payments.gateways.base import PaymentGateway, PaymentGatewayError

logger = logging.getLogger(__name__)

# The 20 fields of the Transaction Processed callback, in the exact order
# Paymob documents. Paths are relative to the callback's ``obj``.
HMAC_FIELDS = (
    "amount_cents",
    "created_at",
    "currency",
    "error_occured",
    "has_parent_transaction",
    "id",
    "integration_id",
    "is_3d_secure",
    "is_auth",
    "is_capture",
    "is_refunded",
    "is_standalone_payment",
    "is_voided",
    "order.id",
    "owner",
    "pending",
    "source_data.pan",
    "source_data.sub_type",
    "source_data.type",
    "success",
)

_REQUIRED_SETTINGS = (
    "PAYMOB_SECRET_KEY",
    "PAYMOB_PUBLIC_KEY",
    "PAYMOB_INTEGRATION_ID",
)


def amount_to_cents(amount) -> int:
    """Decimal price -> integer minor units (x100), never via float."""
    cents = (Decimal(str(amount)) * 100).quantize(Decimal("1"), ROUND_HALF_UP)
    return int(cents)


def _lookup(obj: dict, dotted: str):
    value = obj
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _to_signed_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class PaymobGateway(PaymentGateway):
    def _missing_settings(self):
        return [name for name in _REQUIRED_SETTINGS if not getattr(settings, name, "")]

    def _billing_data(self, subscription) -> dict:
        business = subscription.business
        user = business.user
        return {
            "first_name": user.first_name or business.business_name,
            "last_name": user.last_name or "NA",
            "email": user.email,
            "phone_number": business.phone_number or "NA",
            "country": business.country or "NA",
            "city": business.city or "NA",
            "street": "NA",
            "building": "NA",
            "floor": "NA",
            "apartment": "NA",
            "state": "NA",
        }

    def build_intention_body(self, subscription) -> dict:
        plan = subscription.plan
        cents = amount_to_cents(plan.price)
        body = {
            "amount": cents,
            "currency": plan.currency,
            "payment_methods": [int(settings.PAYMOB_INTEGRATION_ID)],
            # Paymob requires amount == sum of the items' amounts.
            "items": [
                {
                    "name": plan.name,
                    "amount": cents,
                    "description": f"Featured plan: {plan.duration_days} days",
                    "quantity": 1,
                }
            ],
            "billing_data": self._billing_data(subscription),
            # Comes back in the callback as merchant_order_id (P-090 can
            # use it to find the payments.Subscription).
            "special_reference": f"payments-sub-{subscription.pk}",
        }
        if settings.PAYMOB_NOTIFICATION_URL:
            body["notification_url"] = settings.PAYMOB_NOTIFICATION_URL
        if settings.PAYMOB_REDIRECTION_URL:
            body["redirection_url"] = settings.PAYMOB_REDIRECTION_URL
        return body

    def initiate_payment(self, subscription) -> dict:
        missing = self._missing_settings()
        if missing:
            raise PaymentGatewayError(
                "Paymob is not configured, missing: " + ", ".join(missing)
            )

        url = settings.PAYMOB_BASE_URL.rstrip("/") + "/v1/intention/"
        try:
            response = requests.post(
                url,
                json=self.build_intention_body(subscription),
                headers={"Authorization": f"Token {settings.PAYMOB_SECRET_KEY}"},
                timeout=settings.PAYMOB_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise PaymentGatewayError(f"Paymob request failed: {exc}") from exc

        if response.status_code not in (200, 201):
            logger.warning(
                "Paymob create-intention failed: status=%s body=%s",
                response.status_code,
                response.text[:300],
            )
            raise PaymentGatewayError(
                f"Paymob create-intention returned HTTP {response.status_code}"
            )

        try:
            data = response.json()
            client_secret = data["client_secret"]
            order_id = data["intention_order_id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise PaymentGatewayError(
                "Paymob create-intention response is missing client_secret / "
                "intention_order_id"
            ) from exc

        query = urlencode(
            {
                "publicKey": settings.PAYMOB_PUBLIC_KEY,
                "clientSecret": client_secret,
            }
        )
        return {
            "payment_url": f"{settings.PAYMOB_CHECKOUT_BASE_URL}?{query}",
            "gateway_reference": str(order_id),
            "intention_id": str(data.get("id", "")),
        }

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        secret = settings.PAYMOB_WEBHOOK_SECRET
        # An empty secret would let anyone forge a valid signature.
        if not secret or not signature:
            return False
        try:
            obj = json.loads(payload)["obj"]
            if not isinstance(obj, dict):
                return False
        except (ValueError, KeyError, TypeError):
            return False

        signed = "".join(_to_signed_text(_lookup(obj, f)) for f in HMAC_FIELDS)
        expected = hmac.new(
            secret.encode("utf-8"), signed.encode("utf-8"), hashlib.sha512
        ).hexdigest()
        return hmac.compare_digest(expected, str(signature).lower())
