"""BE-06 sweep for the merchant-side orders endpoints (Subscription Gate)."""

import pytest

from core.tests.sweep_factories import client_for as anon_client
from core.tests.sweep_helpers import assert_error_envelope
from monetization.tests.helpers import give_subscription
from orders.tests import urls_map as u
from orders.tests.factories import client_for, make_business

pytestmark = pytest.mark.django_db

MERCHANT_ROUTES = [
    ("get", u.MERCHANT_LIST),
    ("get", u.url(u.MERCHANT_DETAIL, order_id=1)),
    ("post", u.url(u.MERCHANT_STATUS, order_id=1)),
    ("post", u.url(u.MERCHANT_NOTES, order_id=1)),
    ("get", u.url(u.MERCHANT_EVENTS, order_id=1)),
    ("get", u.url(u.PRODUCT_ORDERS, product_id=1)),
    ("post", u.FROM_CHAT),
]
IDS = [f"{m}-{p}" for m, p in MERCHANT_ROUTES]


def _call(client, method, path):
    return getattr(client, method)(path, {}, format="json")


@pytest.mark.parametrize("method,path", MERCHANT_ROUTES, ids=IDS)
def test_anonymous_gets_401(method, path):
    assert _call(anon_client(), method, path).status_code == 401


@pytest.mark.parametrize("method,path", MERCHANT_ROUTES, ids=IDS)
def test_locked_merchant_gets_subscription_required(method, path):
    business = make_business()
    give_subscription(business, "locked")

    response = _call(client_for(business.user), method, path)

    assert_error_envelope(response, 403, "SUBSCRIPTION_REQUIRED")


@pytest.mark.parametrize("method,path", MERCHANT_ROUTES, ids=IDS)
def test_merchant_with_running_trial_is_not_gated(method, path):
    business = make_business()
    give_subscription(business, "trialing", trial_days=5)

    response = _call(client_for(business.user), method, path)

    code = response.json().get("error", {}).get("code")
    assert code != "SUBSCRIPTION_REQUIRED"