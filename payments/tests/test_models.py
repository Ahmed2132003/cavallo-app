"""Model tests for payments.Subscription / Transaction / Invoice (P-089)."""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from businesses.services import create_business_profile
from monetization.models import FeaturedSubscription, Plan
from payments.models import (
    STATUS_CHOICES,
    Invoice,
    Subscription,
    Transaction,
)
from products.models import Product

User = get_user_model()

pytestmark = pytest.mark.django_db


def _make_business(suffix="a"):
    email = f"payments-model-{suffix}@example.com"
    user = User.objects.create_user(
        username=email,
        email=email,
        password="testpass123",
        account_type="business",
    )
    return create_business_profile(
        user=user,
        business_name=f"Payments Model Trader {suffix}",
        business_type="trader",
        country="EG",
        city="Ismailia",
    )


def _make_plan(name="Featured 30", days=30):
    return Plan.objects.create(
        name=name,
        duration_days=days,
        price=Decimal("250.00"),
        currency=Product.CURRENCY_EGP,
    )


def _make_subscription(suffix="a"):
    return Subscription.objects.create(
        business=_make_business(suffix), plan=_make_plan(f"Plan {suffix}")
    )


def _make_transaction(subscription, transaction_id=None):
    return Transaction.objects.create(
        subscription=subscription,
        transaction_id=transaction_id,
        amount=Decimal("250.00"),
        currency=Product.CURRENCY_EGP,
    )


class TestSubscription:
    def test_defaults(self):
        sub = _make_subscription()
        assert sub.status == "pending"
        assert sub.gateway_reference == ""

    def test_status_choices_pinned(self):
        assert [value for value, _ in STATUS_CHOICES] == [
            "pending",
            "completed",
            "failed",
        ]

    def test_plan_is_protected(self):
        sub = _make_subscription()
        with pytest.raises(ProtectedError):
            sub.plan.delete()

    def test_does_not_create_featured_state(self):
        # payments.Subscription is the PAYMENT record; it must never
        # create or activate a monetization.FeaturedSubscription.
        sub = _make_subscription()
        sub.status = "completed"
        sub.save()
        assert FeaturedSubscription.objects.count() == 0


class TestTransaction:
    def test_defaults_and_null_transaction_id(self):
        txn = _make_transaction(_make_subscription())
        assert txn.status == "pending"
        assert txn.transaction_id is None

    def test_many_pending_rows_can_have_null_transaction_id(self):
        sub = _make_subscription()
        _make_transaction(sub)
        _make_transaction(sub)
        assert Transaction.objects.filter(transaction_id__isnull=True).count() == 2

    def test_transaction_id_is_unique(self):
        sub = _make_subscription()
        _make_transaction(sub, "gw-1")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                _make_transaction(sub, "gw-1")

    def test_currency_choices_equal_product_choices(self):
        field = Transaction._meta.get_field("currency")
        assert list(field.choices) == list(Product.CURRENCY_CHOICES)

    def test_status_choices_equal_subscription_choices(self):
        txn_field = Transaction._meta.get_field("status")
        sub_field = Subscription._meta.get_field("status")
        assert list(txn_field.choices) == list(sub_field.choices)


class TestInvoice:
    def test_issued_at_auto_and_pdf_url_optional(self):
        sub = _make_subscription()
        txn = _make_transaction(sub, "gw-inv-1")
        invoice = Invoice.objects.create(subscription=sub, transaction=txn)
        assert invoice.issued_at is not None
        assert invoice.pdf_url is None


class TestNoCardData:
    def test_field_sets_are_pinned(self):
        def names(model):
            return {f.name for f in model._meta.get_fields() if not f.auto_created}

        assert names(Subscription) == {
            "business",
            "plan",
            "status",
            "gateway_reference",
            "created_at",
            "updated_at",
        }
        assert names(Transaction) == {
            "subscription",
            "transaction_id",
            "amount",
            "currency",
            "status",
            "created_at",
            "updated_at",
        }
        assert names(Invoice) == {
            "subscription",
            "transaction",
            "issued_at",
            "pdf_url",
            "created_at",
            "updated_at",
        }

    def test_no_field_looks_like_card_data(self):
        forbidden = ("card", "cvv", "cvc", "pan", "expiry", "token")
        for model in (Subscription, Transaction, Invoice):
            for field in model._meta.get_fields():
                for word in forbidden:
                    assert word not in field.name.lower(), (model, field.name)
