"""Shared test helpers for the payments app (P-089)."""

from decimal import Decimal

from django.contrib.auth import get_user_model

from businesses.services import create_business_profile
from monetization.models import Plan
from payments.models import Subscription
from products.models import Product

User = get_user_model()


def make_business(suffix="a", phone_number="+201001234567"):
    email = f"payments-{suffix}@example.com"
    user = User.objects.create_user(
        username=email,
        email=email,
        password="testpass123",
        first_name="Mona",
        last_name="Ali",
        account_type="business",
    )
    return create_business_profile(
        user=user,
        business_name=f"Payments Trader {suffix}",
        business_type="trader",
        country="EG",
        city="Ismailia",
        phone_number=phone_number,
    )


def make_plan(name="Featured 30", days=30, price="250.00", currency="EGP"):
    return Plan.objects.create(
        name=name,
        duration_days=days,
        price=Decimal(price),
        currency=currency or Product.CURRENCY_EGP,
    )


def make_subscription(suffix="a", **plan_kwargs):
    return Subscription.objects.create(
        business=make_business(suffix), plan=make_plan(**plan_kwargs)
    )


def make_storage_plan(
    code="pro", price_egp="199.00", storage_gb=50, is_custom=False
):
    """
    BE-07: a catalog (storage) Plan by code. update_or_create, because
    BE-01's migration already seeds codes like "pro" / "infinity".
    """
    return Plan.objects.update_or_create(
        code=code,
        defaults={
            "name": code.title(),
            "duration_days": 30,
            "price": Decimal(price_egp or "0"),
            "currency": "EGP",
            "price_egp": Decimal(price_egp) if price_egp is not None else None,
            "storage_bytes": (
                storage_gb * 1024**3 if storage_gb is not None else None
            ),
            "is_custom": is_custom,
            "is_active": True,
            "trial_days": 0,
        },
    )[0]
