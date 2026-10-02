"""Tests for the daily reconciliation job (P-091 STEP 3).

The gateway is always a FakeGateway (or a small subclass): no HTTP, no live
Paymob. The activation path under test is the REAL one
(payments.webhooks.process_webhook_event -> activate_subscription).
"""

import ast
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.utils import timezone

from monetization.models import FeaturedSubscription
from monetization.services import activate_subscription
from payments import tasks as tasks_module
from payments.gateways.base import GatewayTransactionStatus, PaymentGatewayError
from payments.models import Subscription, Transaction
from payments.tasks import RECONCILIATION_GRACE_PERIOD, reconcile_pending_transactions
from payments.tests.factories import make_subscription
from payments.tests.fake_gateway import FakeGateway

pytestmark = pytest.mark.django_db


def gateway_status(sub, **overrides):
    values = dict(
        transaction_id=f"gw-{sub.pk}",
        order_id="4778239",
        merchant_order_id=f"payments-sub-{sub.pk}",
        success=True,
        pending=False,
        is_voided=False,
        is_refunded=False,
        amount_cents=25000,
        currency="EGP",
    )
    values.update(overrides)
    return GatewayTransactionStatus(**values)


def pending_txn(sub, hours=2, amount="250.00"):
    """A pending Transaction (no gateway id yet), created ``hours`` ago."""
    txn = Transaction.objects.create(subscription=sub, amount=amount, currency="EGP")
    Transaction.objects.filter(pk=txn.pk).update(
        created_at=timezone.now() - timedelta(hours=hours)
    )
    txn.refresh_from_db()
    return txn


def run(monkeypatch, gateway):
    monkeypatch.setattr("payments.tasks.get_gateway", lambda: gateway)
    return reconcile_pending_transactions()


class PerSubscriptionGateway(FakeGateway):
    """Different answer per subscription id (value may be an Exception)."""

    def __init__(self, answers):
        super().__init__()
        self.answers = answers

    def check_transaction_status(self, subscription):
        self.status_calls.append(subscription.pk)
        answer = self.answers[subscription.pk]
        if isinstance(answer, Exception):
            raise answer
        return answer


class TestMissedWebhookIsRecovered:
    def test_completed_at_gateway_is_processed_and_featured(self, monkeypatch):
        sub = make_subscription()
        txn = pending_txn(sub)

        counts = run(monkeypatch, FakeGateway(gateway_status(sub)))

        assert counts == {"checked": 1, "processed": 1, "skipped": 0, "errors": 0}
        txn.refresh_from_db()
        sub.refresh_from_db()
        assert txn.status == "completed"
        assert txn.transaction_id == f"gw-{sub.pk}"
        assert sub.status == "completed"
        assert FeaturedSubscription.objects.filter(
            business=sub.business, is_active=True
        ).exists()
        sub.business.refresh_from_db()
        assert sub.business.is_featured is True

    def test_activation_is_called_exactly_once(self, monkeypatch):
        sub = make_subscription()
        pending_txn(sub)
        with patch(
            "payments.webhooks.activate_subscription", wraps=activate_subscription
        ) as spy:
            run(monkeypatch, FakeGateway(gateway_status(sub)))
        assert spy.call_count == 1

    def test_goes_through_the_shared_webhook_function(self, monkeypatch):
        sub = make_subscription()
        pending_txn(sub)
        with patch(
            "payments.tasks.process_webhook_event",
            wraps=tasks_module.process_webhook_event,
        ) as shared:
            run(monkeypatch, FakeGateway(gateway_status(sub)))
        assert shared.call_count == 1
        event = shared.call_args.args[0]
        assert event.transaction_id == f"gw-{sub.pk}"
        assert event.success is True

    def test_failed_at_gateway_is_marked_failed_and_not_featured(self, monkeypatch):
        sub = make_subscription()
        txn = pending_txn(sub)

        counts = run(monkeypatch, FakeGateway(gateway_status(sub, success=False)))

        assert counts["processed"] == 1
        txn.refresh_from_db()
        sub.refresh_from_db()
        assert (txn.status, sub.status) == ("failed", "failed")
        assert FeaturedSubscription.objects.count() == 0

    def test_amount_mismatch_is_rejected_by_the_shared_check(self, monkeypatch):
        sub = make_subscription()
        txn = pending_txn(sub)

        run(monkeypatch, FakeGateway(gateway_status(sub, amount_cents=100)))

        txn.refresh_from_db()
        sub.refresh_from_db()
        assert (txn.status, sub.status) == ("pending", "pending")
        assert FeaturedSubscription.objects.count() == 0


class TestLeftAlone:
    @pytest.mark.parametrize(
        "flags",
        [
            {"pending": True, "success": False},
            {"is_voided": True, "success": False},
            {"is_refunded": True},
        ],
    )
    def test_not_final_at_gateway_is_untouched(self, monkeypatch, flags):
        sub = make_subscription()
        txn = pending_txn(sub)
        before = (txn.updated_at, sub.updated_at)

        counts = run(monkeypatch, FakeGateway(gateway_status(sub, **flags)))

        assert counts == {"checked": 1, "processed": 0, "skipped": 1, "errors": 0}
        txn.refresh_from_db()
        sub.refresh_from_db()
        assert (txn.status, sub.status) == ("pending", "pending")
        assert txn.transaction_id is None
        assert (txn.updated_at, sub.updated_at) == before
        assert FeaturedSubscription.objects.count() == 0

    def test_no_gateway_transaction_is_untouched(self, monkeypatch):
        sub = make_subscription()
        txn = pending_txn(sub)

        counts = run(monkeypatch, FakeGateway(None))

        assert counts["skipped"] == 1
        txn.refresh_from_db()
        assert txn.status == "pending"

    def test_within_grace_period_is_skipped_without_asking_the_gateway(
        self, monkeypatch
    ):
        sub = make_subscription()
        txn = pending_txn(sub, hours=0)
        gateway = FakeGateway(gateway_status(sub))

        counts = run(monkeypatch, gateway)

        assert gateway.status_calls == []
        assert counts == {"checked": 0, "processed": 0, "skipped": 0, "errors": 0}
        txn.refresh_from_db()
        assert txn.status == "pending"

    def test_just_inside_and_just_outside_the_grace_period(self, monkeypatch):
        inside = make_subscription("inside")
        outside = make_subscription("outside")
        txn_in = pending_txn(inside, hours=0)
        txn_out = pending_txn(outside, hours=0)
        Transaction.objects.filter(pk=txn_in.pk).update(
            created_at=timezone.now()
            - RECONCILIATION_GRACE_PERIOD
            + timedelta(minutes=1)
        )
        Transaction.objects.filter(pk=txn_out.pk).update(
            created_at=timezone.now()
            - RECONCILIATION_GRACE_PERIOD
            - timedelta(minutes=1)
        )
        gateway = FakeGateway(None)

        run(monkeypatch, gateway)

        assert gateway.status_calls == [outside.pk]

    def test_grace_period_is_one_hour(self):
        assert RECONCILIATION_GRACE_PERIOD == timedelta(hours=1)

    def test_already_completed_or_failed_rows_are_not_asked_about(self, monkeypatch):
        done = make_subscription("done")
        Transaction.objects.create(
            subscription=done, amount="250.00", currency="EGP", status="completed"
        )
        failed = make_subscription("failed")
        pending_txn(failed)
        Subscription.objects.filter(pk=failed.pk).update(status="failed")
        gateway = FakeGateway(None)

        run(monkeypatch, gateway)

        assert gateway.status_calls == []


class TestIdempotency:
    def test_second_run_does_not_activate_again(self, monkeypatch):
        sub = make_subscription()
        pending_txn(sub)
        gateway = FakeGateway(gateway_status(sub))

        with patch(
            "payments.webhooks.activate_subscription", wraps=activate_subscription
        ) as spy:
            first = run(monkeypatch, gateway)
            second = run(monkeypatch, gateway)

        assert first["processed"] == 1
        assert second == {"checked": 0, "processed": 0, "skipped": 0, "errors": 0}
        assert spy.call_count == 1
        assert gateway.status_calls == [sub.pk]
        assert FeaturedSubscription.objects.count() == 1

    def test_webhook_already_processed_it_means_no_second_activation(self, monkeypatch):
        # The webhook completed the transaction, but a stale gateway answer
        # still says completed: the shared function reports a duplicate.
        sub = make_subscription()
        txn = pending_txn(sub)
        gateway = FakeGateway(gateway_status(sub))
        run(monkeypatch, gateway)

        Transaction.objects.filter(pk=txn.pk).update(status="pending")
        Subscription.objects.filter(pk=sub.pk).update(status="completed")
        with patch(
            "payments.webhooks.activate_subscription", wraps=activate_subscription
        ) as spy:
            run(monkeypatch, gateway)

        assert spy.call_count == 0
        assert FeaturedSubscription.objects.count() == 1

    def test_several_pending_rows_of_one_subscription_ask_once(self, monkeypatch):
        sub = make_subscription()
        pending_txn(sub, hours=5)
        pending_txn(sub, hours=3)
        gateway = FakeGateway(None)

        counts = run(monkeypatch, gateway)

        assert gateway.status_calls == [sub.pk]
        assert counts["checked"] == 1


class TestResilience:
    def test_one_gateway_error_does_not_stop_the_batch(self, monkeypatch):
        bad = make_subscription("bad")
        good = make_subscription("good")
        bad_txn = pending_txn(bad, hours=5)
        good_txn = pending_txn(good, hours=3)
        gateway = PerSubscriptionGateway(
            {bad.pk: PaymentGatewayError("down"), good.pk: gateway_status(good)}
        )

        counts = run(monkeypatch, gateway)

        assert counts == {"checked": 2, "processed": 1, "skipped": 0, "errors": 1}
        bad_txn.refresh_from_db()
        good_txn.refresh_from_db()
        assert bad_txn.status == "pending"
        assert good_txn.status == "completed"

    def test_unexpected_processing_error_is_counted_not_raised(self, monkeypatch):
        sub = make_subscription()
        txn = pending_txn(sub)
        with patch("payments.tasks.process_webhook_event", side_effect=RuntimeError):
            counts = run(monkeypatch, FakeGateway(gateway_status(sub)))
        assert counts["errors"] == 1
        txn.refresh_from_db()
        assert txn.status == "pending"


class TestSingleImplementationAndSchedule:
    def test_tasks_module_never_activates_or_writes_status_itself(self):
        tree = ast.parse(Path(tasks_module.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert "activate_subscription" not in imported
        assert not any(name.startswith("monetization") for name in imported)
        assert "payments.gateways.paymob" not in imported
        assigned = {
            target.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute)
        }
        assert "status" not in assigned

    def test_task_is_registered_with_celery(self):
        from config.celery import app

        app.loader.import_default_modules()
        assert "payments.reconcile_pending_transactions" in app.tasks

    def test_task_is_scheduled_daily_in_beat(self):
        entries = [
            entry
            for entry in settings.CELERY_BEAT_SCHEDULE.values()
            if entry["task"] == "payments.reconcile_pending_transactions"
        ]
        assert len(entries) == 1
        schedule = entries[0]["schedule"]
        assert schedule.hour == {1}
        assert schedule.minute == {0}
