"""A trivial second gateway, used to prove nothing is Paymob-specific."""

from payments.gateways.base import PaymentGateway


class FakeGateway(PaymentGateway):
    def initiate_payment(self, subscription):
        return {
            "payment_url": f"https://fake-pay.example/checkout/{subscription.pk}",
            "gateway_reference": f"fake-{subscription.pk}",
        }

    def verify_webhook_signature(self, payload, signature):
        return signature == "valid"
