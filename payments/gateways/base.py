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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from payments.models import Subscription


class PaymentGatewayError(Exception):
    """
    Any failure talking to / configuring a gateway (network error,
    non-2xx answer, unexpected response shape, missing credentials).
    Calling code catches this one type regardless of the provider.
    """


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
