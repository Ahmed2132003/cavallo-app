"""A trivial second gateway, used to prove nothing is Paymob-specific."""

from payments.gateways.base import PaymentGateway


class FakeGateway(PaymentGateway):
    """
    ``status_result`` is what check_transaction_status() returns (a
    GatewayTransactionStatus, None, or an Exception instance to raise).
    ``status_calls`` records every subscription it was asked about, so
    tests can assert "never called" / "called once".
    """

    def __init__(self, status_result=None):
        self.status_result = status_result
        self.status_calls = []

    def check_transaction_status(self, subscription):
        self.status_calls.append(subscription.pk)
        if isinstance(self.status_result, Exception):
            raise self.status_result
        return self.status_result

    def initiate_payment(self, subscription):
        return {
            "payment_url": f"https://fake-pay.example/checkout/{subscription.pk}",
            "gateway_reference": f"fake-{subscription.pk}",
        }

    def verify_webhook_signature(self, payload, signature):
        return signature == "valid"
