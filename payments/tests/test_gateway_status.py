"""Tests for GatewayTransactionStatus and check_transaction_status (P-091 STEP 1).

HTTP is mocked with ``responses``; shapes follow Paymob's documented
transaction inquiry answer (same object as the Transaction Processed
callback). No live Paymob call is ever made.
"""

import json

import pytest
import requests
import responses

from payments.gateways.base import (
    GatewayTransactionStatus,
    PaymentGateway,
    PaymentGatewayError,
)
from payments.gateways.paymob import PaymobGateway, merchant_reference
from payments.tests.fake_gateway import FakeGateway
from payments.tests.factories import make_subscription

pytestmark = pytest.mark.django_db

AUTH_URL = "https://accept.paymob.com/api/auth/tokens"
INQUIRY_URL = "https://accept.paymob.com/api/ecommerce/orders/transaction_inquiry"


@pytest.fixture
def paymob_settings(settings):
    settings.PAYMOB_API_KEY = "test-api-key"
    settings.PAYMOB_BASE_URL = "https://accept.paymob.com"
    settings.PAYMOB_TIMEOUT_SECONDS = 5
    return settings


def _txn(**overrides):
    data = {
        "id": 2556706,
        "pending": False,
        "success": True,
        "is_voided": False,
        "is_refunded": False,
        "amount_cents": 25000,
        "currency": "EGP",
        "order": {"id": 4778239, "merchant_order_id": "payments-sub-1"},
    }
    data.update(overrides)
    return data


def _status(**overrides):
    values = dict(
        transaction_id="1",
        order_id="2",
        merchant_order_id="payments-sub-1",
        success=True,
        pending=False,
        is_voided=False,
        is_refunded=False,
        amount_cents=25000,
        currency="EGP",
    )
    values.update(overrides)
    return GatewayTransactionStatus(**values)


def _mock_auth(status=201, **kwargs):
    body = kwargs or {"json": {"token": "tok-123"}}
    responses.add(responses.POST, AUTH_URL, status=status, **body)


class TestGatewayTransactionStatus:
    def test_success_is_completed(self):
        assert _status().status == "completed"

    def test_final_non_success_is_failed(self):
        assert _status(success=False).status == "failed"

    @pytest.mark.parametrize(
        "flags",
        [
            {"pending": True},
            {"pending": True, "success": False},
            {"is_voided": True},
            {"is_refunded": True},
        ],
    )
    def test_not_final_states_are_pending(self, flags):
        assert _status(**flags).status == "pending"

    def test_is_immutable(self):
        with pytest.raises(AttributeError):
            _status().success = False


class TestPaymobCheckTransactionStatus:
    @responses.activate
    def test_completed_by_order_id(self, paymob_settings):
        sub = make_subscription()
        sub.gateway_reference = "4778239"
        sub.save()
        _mock_auth()
        responses.add(responses.POST, INQUIRY_URL, json=_txn(), status=200)

        result = PaymobGateway().check_transaction_status(sub)

        assert result.status == "completed"
        assert result.transaction_id == "2556706"
        assert result.order_id == "4778239"
        assert result.merchant_order_id == "payments-sub-1"
        assert result.amount_cents == 25000
        assert result.currency == "EGP"
        assert json.loads(responses.calls[0].request.body) == {
            "api_key": "test-api-key"
        }
        inquiry = responses.calls[1].request
        assert json.loads(inquiry.body) == {"order_id": 4778239}
        assert inquiry.headers["Authorization"] == "Bearer tok-123"

    @responses.activate
    def test_falls_back_to_merchant_reference(self, paymob_settings):
        sub = make_subscription()
        assert sub.gateway_reference == ""
        _mock_auth()
        responses.add(responses.POST, INQUIRY_URL, json=_txn(), status=200)

        PaymobGateway().check_transaction_status(sub)

        assert json.loads(responses.calls[1].request.body) == {
            "merchant_order_id": f"payments-sub-{sub.pk}"
        }
        assert merchant_reference(sub) == f"payments-sub-{sub.pk}"

    @responses.activate
    def test_failed(self, paymob_settings):
        _mock_auth()
        responses.add(responses.POST, INQUIRY_URL, json=_txn(success=False), status=200)
        result = PaymobGateway().check_transaction_status(make_subscription())
        assert result.status == "failed"

    @responses.activate
    def test_still_pending(self, paymob_settings):
        _mock_auth()
        responses.add(
            responses.POST,
            INQUIRY_URL,
            json=_txn(success=False, pending=True),
            status=200,
        )
        result = PaymobGateway().check_transaction_status(make_subscription())
        assert result.status == "pending"

    @responses.activate
    def test_voided_is_not_final(self, paymob_settings):
        _mock_auth()
        responses.add(
            responses.POST, INQUIRY_URL, json=_txn(is_voided=True), status=200
        )
        result = PaymobGateway().check_transaction_status(make_subscription())
        assert result.status == "pending"

    @responses.activate
    def test_text_booleans_are_understood_and_unknown_fail_closed(
        self, paymob_settings
    ):
        _mock_auth()
        responses.add(
            responses.POST,
            INQUIRY_URL,
            json=_txn(success="true", pending="maybe"),
            status=200,
        )
        result = PaymobGateway().check_transaction_status(make_subscription())
        assert result.success is True
        assert result.pending is False

    @responses.activate
    def test_bad_amount_becomes_none(self, paymob_settings):
        _mock_auth()
        responses.add(
            responses.POST, INQUIRY_URL, json=_txn(amount_cents="250"), status=200
        )
        result = PaymobGateway().check_transaction_status(make_subscription())
        assert result.amount_cents is None

    @responses.activate
    def test_404_means_no_transaction(self, paymob_settings):
        _mock_auth()
        responses.add(
            responses.POST, INQUIRY_URL, json={"detail": "Not found"}, status=404
        )
        assert PaymobGateway().check_transaction_status(make_subscription()) is None

    @responses.activate
    def test_inquiry_server_error_raises(self, paymob_settings):
        _mock_auth()
        responses.add(responses.POST, INQUIRY_URL, body="boom", status=500)
        with pytest.raises(PaymentGatewayError):
            PaymobGateway().check_transaction_status(make_subscription())

    @responses.activate
    def test_answer_without_id_raises(self, paymob_settings):
        _mock_auth()
        responses.add(responses.POST, INQUIRY_URL, json={"success": True}, status=200)
        with pytest.raises(PaymentGatewayError):
            PaymobGateway().check_transaction_status(make_subscription())

    @responses.activate
    def test_non_json_answer_raises(self, paymob_settings):
        _mock_auth()
        responses.add(responses.POST, INQUIRY_URL, body="<html>", status=200)
        with pytest.raises(PaymentGatewayError):
            PaymobGateway().check_transaction_status(make_subscription())

    @responses.activate
    def test_auth_failure_raises_and_no_inquiry_is_sent(self, paymob_settings):
        responses.add(responses.POST, AUTH_URL, json={"detail": "no"}, status=401)
        with pytest.raises(PaymentGatewayError):
            PaymobGateway().check_transaction_status(make_subscription())
        assert len(responses.calls) == 1

    @responses.activate
    def test_auth_answer_without_token_raises(self, paymob_settings):
        _mock_auth(json={"profile": {}})
        with pytest.raises(PaymentGatewayError):
            PaymobGateway().check_transaction_status(make_subscription())

    @responses.activate
    def test_network_error_raises(self, paymob_settings):
        _mock_auth()
        responses.add(
            responses.POST, INQUIRY_URL, body=requests.ConnectionError("down")
        )
        with pytest.raises(PaymentGatewayError):
            PaymobGateway().check_transaction_status(make_subscription())

    def test_missing_api_key_raises_without_any_http_call(self, paymob_settings):
        paymob_settings.PAYMOB_API_KEY = ""
        with responses.RequestsMock() as rsps:
            with pytest.raises(PaymentGatewayError):
                PaymobGateway().check_transaction_status(make_subscription())
            assert len(rsps.calls) == 0


class TestInterfaceAndFake:
    def test_method_is_part_of_the_abstract_interface(self):
        class NoStatus(PaymentGateway):
            def initiate_payment(self, subscription):
                return {}

            def verify_webhook_signature(self, payload, signature):
                return False

        with pytest.raises(TypeError):
            NoStatus()

    def test_fake_gateway_returns_the_configured_result_and_records_calls(self):
        sub = make_subscription()
        expected = _status()
        gateway = FakeGateway(status_result=expected)
        assert gateway.check_transaction_status(sub) is expected
        assert gateway.status_calls == [sub.pk]

    def test_fake_gateway_can_raise(self):
        gateway = FakeGateway(status_result=PaymentGatewayError("down"))
        with pytest.raises(PaymentGatewayError):
            gateway.check_transaction_status(make_subscription())
