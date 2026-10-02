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
