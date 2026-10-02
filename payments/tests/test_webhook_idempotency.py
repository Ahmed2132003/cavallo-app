"""
Part P-090 (STEP 3): the critical idempotency proofs.

Duplicates are proven with a SPY on activate_subscription (call count),
not only by inspecting the end state. The spy wraps the REAL function,
so database effects are real too.
"""

import ast
from pathlib import Path
from unittest import mock

import pytest
import responses
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.permissions import AllowAny
from rest_framework.test import APIClient

from businesses.models import BusinessProfile
from monetization.models import FeaturedSubscription
from monetization.services import deactivate_subscriptions
from payments import views as views_module
from payments import webhooks as webhooks_module
from payments.models import Subscription, Transaction
from payments.services import initiate_subscription_payment
from payments.tests.factories import make_business, make_plan
from payments.tests.webhook_helpers import (
    ORDER_ID,
    SECRET,
    build_payload,
    db_snapshot,
    make_pending_payment,
    post_webhook,
    sign,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture(autouse=True)
def configured(settings):
    settings.PAYMOB_WEBHOOK_SECRET = SECRET
    return settings


@pytest.fixture
def activate_spy():
    with mock.patch.object(
        webhooks_module,
        "activate_subscription",
        wraps=webhooks_module.activate_subscription,
    ) as spy:
        yield spy


class TestDuplicateDelivery:
    def test_same_payload_twice_activates_exactly_once(self, client, activate_spy):
        make_pending_payment()
        payload = build_payload()

        first = post_webhook(client, payload)
        second = post_webhook(client, payload)

        assert first.status_code == 200
        assert second.status_code == 200
        assert activate_spy.call_count == 1
        assert FeaturedSubscription.objects.count() == 1
        assert Transaction.objects.count() == 1

    def test_duplicate_changes_nothing(self, client, activate_spy):
        make_pending_payment()
        payload = build_payload()
        post_webhook(client, payload)
        featured = FeaturedSubscription.objects.get()
        expires_before = featured.expires_at
        before = db_snapshot()

        response = post_webhook(client, payload)

        assert response.status_code == 200
        assert db_snapshot() == before
        featured.refresh_from_db()
        assert featured.expires_at == expires_before
        assert activate_spy.call_count == 1

    def test_many_redeliveries_still_one_activation(self, client, activate_spy):
        make_pending_payment()
        payload = build_payload()

        statuses = [post_webhook(client, payload).status_code for _ in range(5)]

        assert statuses == [200] * 5
        assert activate_spy.call_count == 1
        assert FeaturedSubscription.objects.count() == 1

    def test_replay_after_expiry_does_not_reactivate(self, client, activate_spy):
        subscription, _ = make_pending_payment()
        payload = build_payload()
        post_webhook(client, payload)
        deactivate_subscriptions(FeaturedSubscription.objects.all())
        business = BusinessProfile.objects.get(pk=subscription.business_id)
        assert business.is_featured is False

        response = post_webhook(client, payload)

        assert response.status_code == 200
        assert activate_spy.call_count == 1
        business.refresh_from_db()
        assert business.is_featured is False
        assert FeaturedSubscription.objects.filter(is_active=True).count() == 0

    def test_replayed_failure_stays_failed(self, client, activate_spy):
        _, txn = make_pending_payment()
        payload = build_payload(success=False)
        assert post_webhook(client, payload).status_code == 200
        before = db_snapshot()

        response = post_webhook(client, payload)

        assert response.status_code == 200
        assert db_snapshot() == before
        activate_spy.assert_not_called()
        txn.refresh_from_db()
        assert txn.status == "failed"

    def test_tampered_replay_of_a_completed_transaction_is_rejected(
        self, client, activate_spy
    ):
        make_pending_payment()
        payload = build_payload()
        post_webhook(client, payload)
        stolen_signature = sign(payload)
        tampered = build_payload(amount_cents=1)
        before = db_snapshot()

        response = post_webhook(client, tampered, hmac_value=stolen_signature)

        assert response.status_code == 400
        assert db_snapshot() == before
        assert activate_spy.call_count == 1

    def test_invalid_signature_never_reaches_processing(self, client):
        with mock.patch.object(views_module, "process_webhook_event") as process:
            response = post_webhook(client, build_payload(), hmac_value="deadbeef")
        assert response.status_code == 400
        process.assert_not_called()


class TestDoublePayment:
    def test_second_successful_transaction_does_not_activate_again(
        self, client, activate_spy
    ):
        subscription, _ = make_pending_payment()

        first = post_webhook(client, build_payload(transaction_id=900001))
        second = post_webhook(client, build_payload(transaction_id=900002))

        assert first.status_code == 200
        assert second.status_code == 200
        assert activate_spy.call_count == 1
        assert FeaturedSubscription.objects.count() == 1
        subscription.refresh_from_db()
        assert subscription.status == "completed"


class TestLocking:
    def test_processing_locks_the_payment_rows(self, client):
        make_pending_payment()

        with CaptureQueriesContext(connection) as ctx:
            response = post_webhook(client, build_payload())

        assert response.status_code == 200
        locking = [q["sql"].lower() for q in ctx.captured_queries]
        assert any(
            "payments_subscription" in sql and "for update" in sql for sql in locking
        )
        assert any(
            "payments_transaction" in sql and "for update" in sql for sql in locking
        )


class TestEndToEnd:
    @responses.activate
    def test_initiate_then_signed_webhook_activates_once(
        self, client, settings, activate_spy
    ):
        settings.PAYMOB_SECRET_KEY = "egy_sk_test_x"
        settings.PAYMOB_PUBLIC_KEY = "egy_pk_test_x"
        settings.PAYMOB_INTEGRATION_ID = "111111"
        responses.add(
            responses.POST,
            settings.PAYMOB_BASE_URL.rstrip("/") + "/v1/intention/",
            json={
                "id": "intention-1",
                "intention_order_id": ORDER_ID,
                "client_secret": "egy_csk_test_abc",
            },
            status=201,
        )
        business = make_business("e2e")
        plan = make_plan()

        initiate_subscription_payment(business, plan)

        subscription = Subscription.objects.get(business=business)
        assert subscription.gateway_reference == str(ORDER_ID)
        payload = build_payload(
            order_id=ORDER_ID,
            merchant_order_id=f"payments-sub-{subscription.pk}",
        )

        first = post_webhook(client, payload)
        replay = post_webhook(client, payload)

        assert first.status_code == 200
        assert replay.status_code == 200
        assert activate_spy.call_count == 1
        subscription.refresh_from_db()
        assert subscription.status == "completed"
        business.refresh_from_db()
        assert business.is_featured is True
        assert FeaturedSubscription.objects.filter(business=business).count() == 1


def _tree(module):
    return ast.parse(Path(module.__file__).read_text(encoding="utf-8"))


def _imported_modules(tree):
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


class TestBoundaries:
    def test_no_concrete_gateway_is_imported(self):
        for module in (webhooks_module, views_module):
            imported = _imported_modules(_tree(module))
            assert "payments.gateways.paymob" not in imported

    def test_activation_only_through_the_sanctioned_function(self):
        webhooks_tree = _tree(webhooks_module)
        monetization_imports = {
            alias.name
            for node in ast.walk(webhooks_tree)
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("monetization")
            for alias in node.names
        }
        assert monetization_imports == {"activate_subscription"}
        assert not any(
            m.startswith("monetization") for m in _imported_modules(_tree(views_module))
        )

    def test_webhook_code_never_writes_featured_state_itself(self):
        forbidden = {"FeaturedSubscription", "is_featured", "is_active"}
        for module in (webhooks_module, views_module):
            used = set()
            for node in ast.walk(_tree(module)):
                if isinstance(node, ast.Name):
                    used.add(node.id)
                elif isinstance(node, ast.Attribute):
                    used.add(node.attr)
                elif isinstance(node, ast.keyword) and node.arg:
                    used.add(node.arg)
                elif isinstance(node, ast.alias):
                    used.add(node.name)
            assert not (forbidden & used)

    def test_works_when_csrf_checks_are_enforced(self):
        strict_client = APIClient(enforce_csrf_checks=True)
        response = post_webhook(strict_client, build_payload())
        assert response.status_code == 200

    def test_view_is_open_by_design_and_unthrottled(self):
        view = views_module.PaymobWebhookView
        assert view.authentication_classes == []
        assert view.permission_classes == [AllowAny]
        assert view.throttle_classes == []
