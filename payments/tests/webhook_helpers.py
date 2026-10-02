"""
Part P-090: shared helpers for the webhook tests.

sign() is an INDEPENDENT implementation of Paymob's documented HMAC
(SHA-512 over the concatenated values of HMAC_FIELDS), so the tests do
not just call the production verifier to produce their own signatures.
"""

import hashlib
import hmac
import json

from payments.gateways.paymob import HMAC_FIELDS
from payments.models import Subscription, Transaction
from payments.tests.factories import make_subscription

WEBHOOK_URL = "/api/v1/payments/webhook/paymob/"
SECRET = "test-hmac-secret"
ORDER_ID = 4778239
TRANSACTION_ID = 900001


def build_payload(
    *,
    transaction_id=TRANSACTION_ID,
    order_id=ORDER_ID,
    merchant_order_id="payments-sub-1",
    success=True,
    pending=False,
    amount_cents=25000,
    currency="EGP",
) -> dict:
    return {
        "type": "TRANSACTION",
        "obj": {
            "id": transaction_id,
            "pending": pending,
            "amount_cents": amount_cents,
            "success": success,
            "is_auth": False,
            "is_capture": False,
            "is_standalone_payment": True,
            "is_voided": False,
            "is_refunded": False,
            "is_3d_secure": True,
            "integration_id": 111111,
            "has_parent_transaction": False,
            "order": {"id": order_id, "merchant_order_id": merchant_order_id},
            "created_at": "2026-10-02T10:00:00.000000",
            "currency": currency,
            "error_occured": False,
            "owner": 1000,
            "source_data": {"pan": "2346", "sub_type": "MasterCard", "type": "card"},
        },
    }


def _signed_text(obj: dict, dotted: str) -> str:
    value = obj
    for part in dotted.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def sign(payload: dict, secret: str = SECRET) -> str:
    text = "".join(_signed_text(payload["obj"], f) for f in HMAC_FIELDS)
    return hmac.new(
        secret.encode("utf-8"), text.encode("utf-8"), hashlib.sha512
    ).hexdigest()


def post_webhook(client, payload, *, secret=SECRET, hmac_value=None, send_hmac=True):
    """POST ``payload`` as JSON; the HMAC goes in the ``hmac`` query param."""
    if hmac_value is None:
        hmac_value = sign(payload, secret)
    url = WEBHOOK_URL + (f"?hmac={hmac_value}" if send_hmac else "")
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def make_pending_payment(suffix="a", gateway_reference=str(ORDER_ID)):
    """A payments.Subscription + pending Transaction, as P-089 leaves them."""
    subscription = make_subscription(suffix)
    subscription.gateway_reference = gateway_reference
    subscription.save(update_fields=["gateway_reference", "updated_at"])
    txn = Transaction.objects.create(
        subscription=subscription,
        transaction_id=None,
        amount=subscription.plan.price,
        currency=subscription.plan.currency,
    )
    return subscription, txn


def db_snapshot() -> dict:
    """Everything a webhook could change, read straight from the DB."""
    from businesses.models import BusinessProfile
    from monetization.models import FeaturedSubscription

    return {
        "subscriptions": list(
            Subscription.objects.order_by("pk").values_list(
                "pk", "status", "gateway_reference"
            )
        ),
        "transactions": list(
            Transaction.objects.order_by("pk").values_list(
                "pk", "status", "transaction_id"
            )
        ),
        "featured_rows": FeaturedSubscription.objects.count(),
        "featured_flags": list(
            BusinessProfile.objects.order_by("pk").values_list("pk", "is_featured")
        ),
    }
