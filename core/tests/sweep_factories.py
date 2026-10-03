"""
Small data factories shared by the P-096 sweep tests (step 2 onwards).

Kept separate from core/tests/sweep_helpers.py (assertions only) so
each file has one job. Nothing here touches production code.
"""

import itertools

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from businesses.services import create_business_profile
from content.models import Post

User = get_user_model()

_counter = itertools.count(1)


def make_user(account_type="customer", prefix="sweep"):
    email = f"{prefix}-{account_type}-{next(_counter)}@example.com"
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type=account_type,
    )


def make_business(name="Sweep Biz"):
    return create_business_profile(
        user=make_user("business", "sweep-biz"),
        business_name=name,
        business_type="trader",
        country="Egypt",
        city="Cairo",
    )


def make_post(business, *, published=True, caption="sweep post"):
    extra = {"status": Post.Status.PUBLISHED} if published else {}
    return Post.objects.create(business=business, caption=caption, **extra)


def client_for(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def garbage_token_client():
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    return client
