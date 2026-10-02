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

from payments.gateways.base import (
    GatewayTransactionStatus,
    PaymentGateway,
    PaymentGatewayError,
)

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


# Legacy Accept API (auth + transaction inquiry), used by P-091 reconciliation.
# https://developers.paymob.com/paymob-docs/developers (Transactions API)
AUTH_TOKEN_PATH = "/api/auth/tokens"
TRANSACTION_INQUIRY_PATH = "/api/ecommerce/orders/transaction_inquiry"


def merchant_reference(subscription) -> str:
    """
    The special_reference we send when creating the intention. It comes
    back as merchant_order_id, and is how a payment is found again.
    """
    return f"payments-sub-{subscription.pk}"


def _flag(value) -> bool:
    """Fail closed: only a real boolean True / the text "true" counts."""
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _parse_transaction_status(data) -> GatewayTransactionStatus:
    if not isinstance(data, dict) or data.get("id") in (None, ""):
        raise PaymentGatewayError("Paymob inquiry answer has no transaction id")
    order = data.get("order")
    if not isinstance(order, dict):
        order = {}
    amount_cents = data.get("amount_cents")
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
        amount_cents = None
    return GatewayTransactionStatus(
        transaction_id=str(data["id"]),
        order_id="" if order.get("id") is None else str(order["id"]),
        merchant_order_id=str(order.get("merchant_order_id") or ""),
        success=_flag(data.get("success")),
        pending=_flag(data.get("pending")),
        is_voided=_flag(data.get("is_voided")),
        is_refunded=_flag(data.get("is_refunded")),
        amount_cents=amount_cents,
        currency=str(data.get("currency") or ""),
    )


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
            "special_reference": merchant_reference(subscription),
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

    def _auth_token(self) -> str:
        """Short-lived (60 min) token for the legacy API. Not cached: the
        reconciliation job runs once a day."""
        url = settings.PAYMOB_BASE_URL.rstrip("/") + AUTH_TOKEN_PATH
        try:
            response = requests.post(
                url,
                json={"api_key": settings.PAYMOB_API_KEY},
                timeout=settings.PAYMOB_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise PaymentGatewayError(f"Paymob auth request failed: {exc}") from exc

        if response.status_code not in (200, 201):
            logger.warning("Paymob auth failed: status=%s", response.status_code)
            raise PaymentGatewayError(
                f"Paymob auth returned HTTP {response.status_code}"
            )
        try:
            token = response.json()["token"]
        except (ValueError, KeyError, TypeError) as exc:
            raise PaymentGatewayError("Paymob auth response has no token") from exc
        if not token or not isinstance(token, str):
            raise PaymentGatewayError("Paymob auth response has an empty token")
        return token

    def check_transaction_status(self, subscription) -> GatewayTransactionStatus | None:
        if not getattr(settings, "PAYMOB_API_KEY", ""):
            raise PaymentGatewayError(
                "Paymob is not configured, missing: PAYMOB_API_KEY"
            )

        # Prefer Paymob's own order id (stored as gateway_reference by
        # P-089); fall back to our merchant reference.
        reference = (subscription.gateway_reference or "").strip()
        if reference.isdigit():
            payload = {"order_id": int(reference)}
        else:
            payload = {"merchant_order_id": merchant_reference(subscription)}

        token = self._auth_token()
        url = settings.PAYMOB_BASE_URL.rstrip("/") + TRANSACTION_INQUIRY_PATH
        try:
            response = requests.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=settings.PAYMOB_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise PaymentGatewayError(f"Paymob inquiry request failed: {exc}") from exc

        if response.status_code == 404:
            # Paymob knows no transaction for this order (never attempted).
            return None
        if response.status_code != 200:
            logger.warning(
                "Paymob transaction inquiry failed: status=%s body=%s",
                response.status_code,
                response.text[:300],
            )
            raise PaymentGatewayError(
                f"Paymob transaction inquiry returned HTTP {response.status_code}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise PaymentGatewayError("Paymob inquiry answer is not JSON") from exc
        return _parse_transaction_status(data)
