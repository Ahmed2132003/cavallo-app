"""Model tests for monetization.Plan / FeaturedSubscription (Part P-086)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from businesses.services import create_business_profile
from monetization.models import FeaturedSubscription, Plan
from products.models import Product

User = get_user_model()


def _make_business(suffix="a"):
    email = f"monetization-model-{suffix}@example.com"
    user = User.objects.create_user(
        username=email,
        email=email,
        password="testpass123",
        account_type="business",
    )
    return create_business_profile(
        user=user,
        business_name=f"Monetization Model Trader {suffix}",
        business_type="trader",
        country="EG",
        city="Ismailia",
    )


def _make_plan(name="Featured 30", days=30):
    return Plan.objects.create(
        name=name,
        duration_days=days,
        price=Decimal("250.00"),
        currency="EGP",
    )


@pytest.mark.django_db
class TestPlanModel:
    def test_fields_are_stored(self):
        plan = _make_plan()
        plan.refresh_from_db()
        assert plan.name == "Featured 30"
        assert plan.duration_days == 30
        assert plan.price == Decimal("250.00")
        assert plan.currency == "EGP"

    def test_currency_choices_reuse_product_choices_exactly(self):
        field = Plan._meta.get_field("currency")
        assert list(field.choices) == list(Product.CURRENCY_CHOICES)

    def test_full_clean_rejects_zero_duration(self):
        plan = Plan(
            name="Zero", duration_days=0, price=Decimal("1.00"), currency="EGP"
        )
        with pytest.raises(ValidationError):
            plan.full_clean()


@pytest.mark.django_db
class TestFeaturedSubscriptionModel:
    def test_is_active_defaults_to_true(self):
        sub = FeaturedSubscription.objects.create(
            business=_make_business(), plan=_make_plan()
        )
        assert sub.is_active is True

    def test_expires_at_is_computed_from_starts_at_and_plan_duration(self):
        sub = FeaturedSubscription.objects.create(
            business=_make_business(), plan=_make_plan(days=30)
        )
        assert sub.starts_at is not None
        assert sub.expires_at == sub.starts_at + timedelta(days=30)
        sub.refresh_from_db()
        assert sub.expires_at - sub.starts_at == timedelta(days=30)

    def test_expires_at_is_stored_and_never_recomputed(self):
        plan = _make_plan(days=7)
        sub = FeaturedSubscription.objects.create(
            business=_make_business(), plan=plan
        )
        original_start = sub.starts_at
        original_expiry = sub.expires_at

        sub.is_active = False
        sub.save()
        plan.duration_days = 90
        plan.save()

        sub.refresh_from_db()
        assert sub.starts_at == original_start
        assert sub.expires_at == original_expiry

    def test_plan_is_protected_while_referenced(self):
        plan = _make_plan()
        FeaturedSubscription.objects.create(business=_make_business(), plan=plan)
        with pytest.raises(ProtectedError):
            plan.delete()

    def test_two_active_subscriptions_for_one_business_are_rejected(self):
        business = _make_business()
        plan = _make_plan()
        FeaturedSubscription.objects.create(business=business, plan=plan)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                FeaturedSubscription.objects.create(business=business, plan=plan)

    def test_inactive_history_plus_one_active_is_allowed(self):
        business = _make_business()
        plan = _make_plan()
        FeaturedSubscription.objects.create(
            business=business, plan=plan, is_active=False
        )
        FeaturedSubscription.objects.create(
            business=business, plan=plan, is_active=False
        )
        FeaturedSubscription.objects.create(business=business, plan=plan)
        assert (
            FeaturedSubscription.objects.filter(
                business=business, is_active=True
            ).count()
            == 1
        )

    def test_different_businesses_can_each_have_an_active_subscription(self):
        plan = _make_plan()
        FeaturedSubscription.objects.create(business=_make_business("x"), plan=plan)
        FeaturedSubscription.objects.create(business=_make_business("y"), plan=plan)
        assert FeaturedSubscription.objects.filter(is_active=True).count() == 2

    def test_field_set_is_pinned(self):
        names = {f.name for f in FeaturedSubscription._meta.get_fields()}
        assert names == {
            "id",
            "created_at",
            "updated_at",
            "business",
            "plan",
            "starts_at",
            "expires_at",
            "is_active",
        }