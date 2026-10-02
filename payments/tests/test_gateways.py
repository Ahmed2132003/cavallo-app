"""Tests for the gateway interface and PaymobGateway (P-089 STEP 2).

HTTP is mocked with ``responses``; request / response shapes follow
Paymob's documented Intention API. No live Paymob call is ever made.
"""

import hashlib
import hmac
import json
from decimal import Decimal

import pytest
import requests
import responses

from payments.gateways import get_gateway
from payments.gateways.base import PaymentGateway, PaymentGatewayError
from payments.gateways.paymob import PaymobGateway, amount_to_cents
from payments.tests.factories import make_subscription

pytestmark = pytest.mark.django_db

INTENTION_URL = "https://accept.paymob.com/v1/intention/"
HMAC_SECRET = "test-hmac-secret"

# Shape of Paymob's documented create-intention answer (201).
INTENTION_RESPONSE = {
    "id": "pi_test_0001",
    "intention_order_id": 4778239,
    "client_secret": "egy_csk_test_abc123",
    "status": "intended",
    "confirmed": False,
    "payment_keys": [],
}


@pytest.fixture
def paymob_settings(settings):
    settings.PAYMOB_SECRET_KEY = "egy_sk_test_secret"
    settings.PAYMOB_PUBLIC_KEY = "egy_pk_test_public"
    settings.PAYMOB_INTEGRATION_ID = "123456"
    settings.PAYMOB_WEBHOOK_SECRET = HMAC_SECRET
    settings.PAYMOB_BASE_URL = "https://accept.paymob.com"
    settings.PAYMOB_CHECKOUT_BASE_URL = "https://eg.checkout.paymob.com/"
    settings.PAYMOB_NOTIFICATION_URL = "https://api.example.com/hooks/paymob/"
    settings.PAYMOB_REDIRECTION_URL = ""
    settings.PAYMOB_TIMEOUT_SECONDS = 5
    return settings


def _sent_body():
    return json.loads(responses.calls[0].request.body)


class TestInitiatePayment:
    @responses.activate
    def test_success_returns_url_and_reference(self, paymob_settings):
        responses.add(
            responses.POST, INTENTION_URL, json=INTENTION_RESPONSE, status=201
        )
        sub = make_subscription()

        result = PaymobGateway().initiate_payment(sub)

        assert result["gateway_reference"] == "4778239"
        assert result["payment_url"] == (
            "https://eg.checkout.paymob.com/"
            "?publicKey=egy_pk_test_public&clientSecret=egy_csk_test_abc123"
        )

    @responses.activate
    def test_request_matches_documented_contract(self, paymob_settings):
        responses.add(
            responses.POST, INTENTION_URL, json=INTENTION_RESPONSE, status=201
        )
        sub = make_subscription()

        PaymobGateway().initiate_payment(sub)

        request = responses.calls[0].request
        assert request.headers["Authorization"] == "Token egy_sk_test_secret"
        body = _sent_body()
        assert body["amount"] == 25000
        assert body["currency"] == "EGP"
        assert body["payment_methods"] == [123456]
        assert sum(i["amount"] * i["quantity"] for i in body["items"]) == body["amount"]
        assert body["items"][0]["name"] == sub.plan.name
        assert body["special_reference"] == f"payments-sub-{sub.pk}"
        assert body["notification_url"] == "https://api.example.com/hooks/paymob/"
        assert "redirection_url" not in body  # blank setting -> omitted
        billing = body["billing_data"]
        for mandatory in ("first_name", "last_name", "email", "phone_number"):
            assert billing[mandatory]
        assert billing["email"] == sub.business.user.email
        assert billing["phone_number"] == sub.business.phone_number

    @responses.activate
    def test_no_card_data_is_ever_sent(self, paymob_settings):
        responses.add(
            responses.POST, INTENTION_URL, json=INTENTION_RESPONSE, status=201
        )
        PaymobGateway().initiate_payment(make_subscription())

        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)

        sent_keys = {k.lower() for k in keys(_sent_body())}
        for forbidden in ("card", "cvv", "cvc", "pan", "expiry", "card_tokens"):
            assert forbidden not in sent_keys

    def test_amount_to_cents_is_exact(self):
        assert amount_to_cents(Decimal("249.99")) == 24999
        assert amount_to_cents(Decimal("0.29")) == 29
        assert amount_to_cents("250.00") == 25000

    @responses.activate
    def test_not_configured_raises_without_http(self, settings):
        settings.PAYMOB_SECRET_KEY = ""
        settings.PAYMOB_PUBLIC_KEY = ""
        settings.PAYMOB_INTEGRATION_ID = ""
        with pytest.raises(PaymentGatewayError, match="not configured"):
            PaymobGateway().initiate_payment(make_subscription())
        assert len(responses.calls) == 0

    @responses.activate
    @pytest.mark.parametrize("status", [400, 401, 404, 500])
    def test_http_errors_raise(self, paymob_settings, status):
        responses.add(
            responses.POST, INTENTION_URL, json={"detail": "nope"}, status=status
        )
        with pytest.raises(PaymentGatewayError, match=str(status)):
            PaymobGateway().initiate_payment(make_subscription())

    @responses.activate
    def test_network_error_raises(self, paymob_settings):
        responses.add(
            responses.POST, INTENTION_URL, body=requests.ConnectionError("down")
        )
        with pytest.raises(PaymentGatewayError, match="request failed"):
            PaymobGateway().initiate_payment(make_subscription())

    @responses.activate
    def test_unexpected_response_shape_raises(self, paymob_settings):
        responses.add(responses.POST, INTENTION_URL, json={"id": "x"}, status=201)
        with pytest.raises(PaymentGatewayError, match="missing"):
            PaymobGateway().initiate_payment(make_subscription())


# Independent, hand-written vector: values in Paymob's documented order,
# concatenated by hand (booleans lowercase, no separators). It does NOT
# reuse the implementation's field list, so it pins order and format.
SIGNED_STRING = (
    "100"  # amount_cents
    "2020-03-25T18:36:06.248785"  # created_at
    "EGP"  # currency
    "false"  # error_occured
    "false"  # has_parent_transaction
    "2556706"  # id
    "6741"  # integration_id
    "true"  # is_3d_secure
    "false"  # is_auth
    "false"  # is_capture
    "false"  # is_refunded
    "true"  # is_standalone_payment
    "false"  # is_voided
    "4778239"  # order.id
    "1234"  # owner
    "false"  # pending
    "2346"  # source_data.pan
    "Visa"  # source_data.sub_type
    "card"  # source_data.type
    "true"  # success
)


def _callback_obj():
    return {
        "id": 2556706,
        "pending": False,
        "amount_cents": 100,
        "success": True,
        "is_auth": False,
        "is_capture": False,
        "is_standalone_payment": True,
        "is_voided": False,
        "is_refunded": False,
        "is_3d_secure": True,
        "integration_id": 6741,
        "has_parent_transaction": False,
        "order": {"id": 4778239},
        "created_at": "2020-03-25T18:36:06.248785",
        "currency": "EGP",
        "error_occured": False,
        "owner": 1234,
        "source_data": {"pan": "2346", "sub_type": "Visa", "type": "card"},
    }


def _sign(text, secret=HMAC_SECRET):
    return hmac.new(secret.encode(), text.encode(), hashlib.sha512).hexdigest()


def _payload(obj=None):
    return json.dumps({"type": "TRANSACTION", "obj": obj or _callback_obj()}).encode()


class TestVerifyWebhookSignature:
    def test_valid_signature(self, paymob_settings):
        assert PaymobGateway().verify_webhook_signature(
            _payload(), _sign(SIGNED_STRING)
        )

    def test_tampered_payload_rejected(self, paymob_settings):
        obj = _callback_obj()
        obj["amount_cents"] = 1
        assert not PaymobGateway().verify_webhook_signature(
            _payload(obj), _sign(SIGNED_STRING)
        )

    def test_tampered_success_flag_rejected(self, paymob_settings):
        obj = _callback_obj()
        obj["success"] = False
        assert not PaymobGateway().verify_webhook_signature(
            _payload(obj), _sign(SIGNED_STRING)
        )

    def test_wrong_secret_rejected(self, paymob_settings):
        assert not PaymobGateway().verify_webhook_signature(
            _payload(), _sign(SIGNED_STRING, secret="other-secret")
        )

    def test_empty_secret_never_validates(self, settings):
        settings.PAYMOB_WEBHOOK_SECRET = ""
        assert not PaymobGateway().verify_webhook_signature(
            _payload(), _sign(SIGNED_STRING, secret="")
        )

    @pytest.mark.parametrize("signature", ["", "not-hex", "0" * 128])
    def test_bad_signatures_rejected(self, paymob_settings, signature):
        assert not PaymobGateway().verify_webhook_signature(_payload(), signature)

    @pytest.mark.parametrize("payload", [b"", b"not json", b"[]", b"{}", b'{"obj": 5}'])
    def test_malformed_payload_never_raises(self, paymob_settings, payload):
        assert PaymobGateway().verify_webhook_signature(payload, "abc") is False


class TestInterface:
    def test_interface_is_abstract(self):
        with pytest.raises(TypeError):
            PaymentGateway()

    def test_subclass_must_implement_both_methods(self):
        class Half(PaymentGateway):
            def initiate_payment(self, subscription):
                return {}

        with pytest.raises(TypeError):
            Half()

    def test_get_gateway_default_is_paymob(self):
        assert isinstance(get_gateway(), PaymobGateway)

    def test_get_gateway_is_driven_by_setting(self, settings):
        settings.PAYMENT_GATEWAY = "payments.tests.fake_gateway.FakeGateway"
        from payments.tests.fake_gateway import FakeGateway

        assert isinstance(get_gateway(), FakeGateway)
