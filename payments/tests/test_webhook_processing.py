"""
Part P-090 (STEP 2): webhook processing - success, failure, non-final
events, mismatches, unmatched payloads, rollback.

Activation is observed with a spy (mock wraps the REAL
activate_subscription), so we assert both the call arguments and the
real resulting database state.
"""

from unittest import mock

import pytest
from rest_framework.test import APIClient

from businesses.models import BusinessProfile
from monetization.models import FeaturedSubscription
from payments import webhooks as webhooks_module
from payments.models import Subscription, Transaction
from payments.tests.webhook_helpers import (
    ORDER_ID,
    SECRET,
    TRANSACTION_ID,
    build_payload,
    db_snapshot,
    make_pending_payment,
    post_webhook,
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


class TestSuccessfulPayment:
    def test_new_success_completes_rows_and_activates(self, client, activate_spy):
        subscription, txn = make_pending_payment()

        response = post_webhook(client, build_payload())

        assert response.status_code == 200
        txn.refresh_from_db()
        subscription.refresh_from_db()
        assert txn.status == "completed"
        assert txn.transaction_id == str(TRANSACTION_ID)
        assert subscription.status == "completed"

        activate_spy.assert_called_once()
        kwargs = activate_spy.call_args.kwargs
        assert kwargs["business"].pk == subscription.business_id
        assert kwargs["plan"].pk == subscription.plan_id

        featured = FeaturedSubscription.objects.get()
        assert featured.is_active is True
        assert featured.business_id == subscription.business_id
        assert featured.plan_id == subscription.plan_id
        business = BusinessProfile.objects.get(pk=subscription.business_id)
        assert business.is_featured is True

    def test_found_by_merchant_order_id_when_reference_is_unknown(
        self, client, activate_spy
    ):
        subscription, txn = make_pending_payment(gateway_reference="")
        payload = build_payload(
            order_id=555,
            merchant_order_id=f"payments-sub-{subscription.pk}",
        )

        response = post_webhook(client, payload)

        assert response.status_code == 200
        activate_spy.assert_called_once()
        txn.refresh_from_db()
        assert txn.status == "completed"

    def test_success_after_an_earlier_failed_attempt_still_activates(
        self, client, activate_spy
    ):
        subscription, _ = make_pending_payment()

        failed = post_webhook(
            client, build_payload(transaction_id=900000, success=False)
        )
        assert failed.status_code == 200
        subscription.refresh_from_db()
        assert subscription.status == "failed"
        activate_spy.assert_not_called()

        ok = post_webhook(client, build_payload(transaction_id=900001))
        assert ok.status_code == 200

        subscription.refresh_from_db()
        assert subscription.status == "completed"
        assert Transaction.objects.filter(subscription=subscription).count() == 2
        assert Transaction.objects.get(transaction_id="900001").status == "completed"
        activate_spy.assert_called_once()

    def test_activation_error_rolls_everything_back(self, client):
        make_pending_payment()
        before = db_snapshot()

        with mock.patch.object(
            webhooks_module,
            "activate_subscription",
            side_effect=RuntimeError("boom"),
        ):
            response = post_webhook(client, build_payload())

        # The project's exception handler (P-012) turns the unexpected
        # error into a 500 (so the gateway retries) instead of re-raising.
        assert response.status_code == 500
        # The Transaction/Subscription were already switched to
        # "completed" before activation failed; an identical snapshot
        # proves the whole atomic block was rolled back.
        assert db_snapshot() == before


class TestNotASuccessfulPayment:
    def test_failed_payment_is_recorded_without_activation(self, client, activate_spy):
        subscription, txn = make_pending_payment()

        response = post_webhook(client, build_payload(success=False))

        assert response.status_code == 200
        txn.refresh_from_db()
        subscription.refresh_from_db()
        assert txn.status == "failed"
        assert txn.transaction_id == str(TRANSACTION_ID)
        assert subscription.status == "failed"
        activate_spy.assert_not_called()
        assert FeaturedSubscription.objects.count() == 0

    def test_pending_event_changes_nothing(self, client, activate_spy):
        make_pending_payment()
        before = db_snapshot()

        response = post_webhook(client, build_payload(success=False, pending=True))

        assert response.status_code == 200
        assert db_snapshot() == before
        activate_spy.assert_not_called()

    @pytest.mark.parametrize("flag", ["is_voided", "is_refunded"])
    def test_voided_or_refunded_is_never_activated(self, client, activate_spy, flag):
        make_pending_payment()
        before = db_snapshot()
        payload = build_payload()
        payload["obj"][flag] = True

        response = post_webhook(client, payload)

        assert response.status_code == 200
        assert db_snapshot() == before
        activate_spy.assert_not_called()

    @pytest.mark.parametrize(
        "field,value", [("amount_cents", 100), ("currency", "USD")]
    )
    def test_amount_or_currency_mismatch_is_not_activated(
        self, client, activate_spy, field, value
    ):
        make_pending_payment()
        before = db_snapshot()
        payload = build_payload()
        payload["obj"][field] = value

        response = post_webhook(client, payload)

        assert response.status_code == 200
        assert db_snapshot() == before
        activate_spy.assert_not_called()

    def test_payload_matching_no_payment_is_acknowledged_untouched(
        self, client, activate_spy
    ):
        # A correctly-signed event for something we never initiated.
        make_pending_payment(gateway_reference="not-this-one")
        before = db_snapshot()

        response = post_webhook(
            client, build_payload(order_id=ORDER_ID, merchant_order_id="other")
        )

        assert response.status_code == 200
        assert db_snapshot() == before
        activate_spy.assert_not_called()
        assert Subscription.objects.count() == 1
