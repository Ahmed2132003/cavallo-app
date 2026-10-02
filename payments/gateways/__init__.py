from django.conf import settings
from django.utils.module_loading import import_string

from payments.gateways.base import PaymentGateway, PaymentGatewayError

__all__ = ["PaymentGateway", "PaymentGatewayError", "get_gateway"]


def get_gateway() -> PaymentGateway:
    """
    Build the configured gateway (settings.PAYMENT_GATEWAY, a dotted path).
    This is the single place that knows which provider is in use.
    """
    return import_string(settings.PAYMENT_GATEWAY)()
