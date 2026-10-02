"""Tests for payments.services.initiate_subscription_payment (P-089 STEP 3)."""

import ast
import json
from decimal import Decimal
from pathlib import Path

import pytest
import responses

import payments.services as services_module
from monetization.models import FeaturedSubscription
from payments.gateways.base import PaymentGatewayError
from payments.models import Subscription, Transaction
from payments.services import initiate_subscription_payment
from payments.tests.factories import make_business, make_plan
from payments.tests.fake_gateway import FakeGateway

pytestmark = pytest.mark.django_db

INTENTION_URL = "https://accept.paymob.com/v1/intention/"
INTENTION_RESPONSE = {
    "id": "pi_test_0001",
    "intention_order_id": 4778239,
    "client_secret": "egy_csk_test_abc123",
    "status": "intended",
}


@pytest.fixture
def paymob_settings(settings):
    settings.PAYMOB_SECRET_KEY = "egy_sk_test_secret"
    settings.PAYMOB_PUBLIC_KEY = "egy_pk_test_public"
    settings.PAYMOB_INTEGRATION_ID = "123456"
    settings.PAYMOB_WEBHOOK_SECRET = "test-hmac-secret"
    settings.PAYMOB_BASE_URL = "https://accept.paymob.com"
    settings.PAYMOB_CHECKOUT_BASE_URL = "https://eg.checkout.paymob.com/"
    settings.PAYMOB_NOTIFICATION_URL = ""
    settings.PAYMOB_REDIRECTION_URL = ""
    settings.PAYMOB_TIMEOUT_SECONDS = 5
    return settings


class TestWithMockedPaymob:
    @responses.activate
    def test_creates_pending_rows_and_returns_payment_url(self, paymob_settings):
        responses.add(
            responses.POST, INTENTION_URL, json=INTENTION_RESPONSE, status=201
        )
        business = make_business()
        plan = make_plan(price="250.00", currency="EGP")

        url = initiate_subscription_payment(business, plan)

        assert url == (
            "https://eg.checkout.paymob.com/"
            "?publicKey=egy_pk_test_public&clientSecret=egy_csk_test_abc123"
        )
        sub = Subscription.objects.get()
        assert sub.status == "pending"
        assert sub.business == business
        assert sub.plan == plan
        assert sub.gateway_reference == "4778239"
        txn = Transaction.objects.get()
        assert txn.subscription == sub
        assert txn.status == "pending"
        assert txn.transaction_id is None
        assert txn.amount == Decimal("250.00")
        assert txn.currency == "EGP"
        # The gateway saw this payments.Subscription's reference.
        body = json.loads(responses.calls[0].request.body)
        assert body["special_reference"] == f"payments-sub-{sub.pk}"

    @responses.activate
    def test_http_error_marks_rows_failed_and_reraises(self, paymob_settings):
        responses.add(responses.POST, INTENTION_URL, json={"detail": "x"}, status=500)
        with pytest.raises(PaymentGatewayError):
            initiate_subscription_payment(make_business(), make_plan())
        sub = Subscription.objects.get()
        txn = Transaction.objects.get()
        assert sub.status == "failed"
        assert txn.status == "failed"
        assert sub.gateway_reference == ""

    @responses.activate
    def test_default_unconfigured_state_fails_cleanly(self, settings):
        settings.PAYMOB_SECRET_KEY = ""
        settings.PAYMOB_PUBLIC_KEY = ""
        settings.PAYMOB_INTEGRATION_ID = ""
        with pytest.raises(PaymentGatewayError, match="not configured"):
            initiate_subscription_payment(make_business(), make_plan())
        assert len(responses.calls) == 0
        assert Subscription.objects.get().status == "failed"


class TestGatewayIsSwappable:
    def test_injected_fake_gateway(self):
        business = make_business()
        plan = make_plan()

        url = initiate_subscription_payment(business, plan, gateway=FakeGateway())

        sub = Subscription.objects.get()
        assert url == f"https://fake-pay.example/checkout/{sub.pk}"
        assert sub.gateway_reference == f"fake-{sub.pk}"
        assert sub.status == "pending"
        assert Transaction.objects.get().status == "pending"

    @responses.activate
    def test_fake_gateway_selected_by_setting_only(self, settings):
        # No injection, no Paymob settings: only PAYMENT_GATEWAY changes.
        settings.PAYMENT_GATEWAY = "payments.tests.fake_gateway.FakeGateway"
        url = initiate_subscription_payment(make_business(), make_plan())
        assert url.startswith("https://fake-pay.example/checkout/")
        assert len(responses.calls) == 0  # nothing Paymob-shaped happened

    def test_bad_gateway_result_is_a_failure(self):
        class BrokenGateway(FakeGateway):
            def initiate_payment(self, subscription):
                return {"payment_url": ""}

        with pytest.raises(PaymentGatewayError):
            initiate_subscription_payment(
                make_business(), make_plan(), gateway=BrokenGateway()
            )
        assert Subscription.objects.get().status == "failed"
        assert Transaction.objects.get().status == "failed"

    def test_calling_code_does_not_import_a_concrete_gateway(self):
        tree = ast.parse(Path(services_module.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert "payments.gateways.paymob" not in imported
        assert not any(name.startswith("monetization") for name in imported)


class TestBoundaries:
    def test_never_creates_or_activates_featured_state(self):
        business = make_business()
        initiate_subscription_payment(business, make_plan(), gateway=FakeGateway())
        assert FeaturedSubscription.objects.count() == 0
        business.refresh_from_db()
        assert business.is_featured is False

    def test_amount_is_a_snapshot_of_the_plan(self):
        plan = make_plan(price="250.00")
        initiate_subscription_payment(make_business(), plan, gateway=FakeGateway())
        plan.price = Decimal("999.00")
        plan.save()
        assert Transaction.objects.get().amount == Decimal("250.00")

    def test_each_call_creates_a_new_attempt(self):
        business = make_business()
        plan = make_plan()
        initiate_subscription_payment(business, plan, gateway=FakeGateway())
        initiate_subscription_payment(business, plan, gateway=FakeGateway())
        assert Subscription.objects.count() == 2
        assert Transaction.objects.count() == 2
