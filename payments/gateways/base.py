"""
Part P-089 (STEP 2): provider-agnostic payment gateway interface.

Calling code (payments.services, the P-090 webhook view, P-091
reconciliation) depends ONLY on this interface and on get_gateway().
It must never import a concrete gateway (Paymob or any other) directly.
Swapping the provider = a new subclass + the PAYMENT_GATEWAY setting.

PCI SCOPE: initiate_payment() returns a gateway-hosted ``payment_url``.
The user pays on the gateway's page; this system never sees card data.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from payments.models import Subscription


class PaymentGatewayError(Exception):
    """
    Any failure talking to / configuring a gateway (network error,
    non-2xx answer, unexpected response shape, missing credentials).
    Calling code catches this one type regardless of the provider.
    """


GATEWAY_STATUS_COMPLETED = "completed"
GATEWAY_STATUS_FAILED = "failed"
GATEWAY_STATUS_PENDING = "pending"


@dataclass(frozen=True)
class GatewayTransactionStatus:
    """
    What the gateway CURRENTLY says about one payment (P-091 STEP 1).

    The fields deliberately mirror payments.views.WebhookEvent (the P-090
    webhook payload), so the reconciliation job (STEP 2) can feed this
    straight into the SAME processing function the webhook uses. There is
    exactly one implementation of "apply a gateway result".
    """

    transaction_id: str
    order_id: str
    merchant_order_id: str
    success: bool
    pending: bool
    is_voided: bool
    is_refunded: bool
    amount_cents: int | None
    currency: str

    @property
    def status(self) -> str:
        """
        "pending"   not final (still pending, voided or refunded): leave it.
        "completed" final and successful.
        "failed"    final and unsuccessful.
        """
        if self.pending or self.is_voided or self.is_refunded:
            return GATEWAY_STATUS_PENDING
        return GATEWAY_STATUS_COMPLETED if self.success else GATEWAY_STATUS_FAILED


class PaymentGateway(ABC):
    @abstractmethod
    def initiate_payment(self, subscription: "Subscription") -> dict:
        """
        Start a payment for ``subscription`` at the gateway.

        Returns a dict with AT LEAST:
            payment_url:       str, gateway-hosted page to redirect the user to.
            gateway_reference: str, the gateway's reference for this attempt.

        Raises PaymentGatewayError on any failure.
        """

    @abstractmethod
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        True only if ``signature`` is valid for the raw webhook ``payload``.
        Must never raise on malformed input: return False instead.
        """

    @abstractmethod
    def check_transaction_status(
        self, subscription: "Subscription"
    ) -> GatewayTransactionStatus | None:
        """
        Ask the gateway for the CURRENT state of the payment made for
        ``subscription`` (P-091 reconciliation; used when a webhook never
        arrived).

        Looked up by the payment's own references, NOT by a transaction
        id: a locally-pending Transaction has no gateway transaction id
        yet (it is filled in by the webhook, which is exactly the thing
        that went missing).

        Returns None when the gateway has no transaction for this payment
        (e.g. the customer never attempted to pay).
        Raises PaymentGatewayError on any failure (network, auth,
        unexpected answer): the caller must NOT treat that as a result.
        """
