"""BI-02 permissions, IDOR and tenant isolation for every dashboard endpoint."""

import pytest

from analytics.models import ExternalContentMetricSnapshot, ProductDailyStats
from analytics.tests.bi02_helpers import (
    RANGE,
    at,
    DAY1,
    build_scenario,
    make_business,
    make_customer,
    make_order,
    make_post,
    make_product,
    url,
)
from core.tests.sweep_factories import client_for, garbage_token_client
from monetization.tests.helpers import give_subscription
from core.tests.sweep_helpers import (
    assert_error_envelope,
    assert_forbidden,
    assert_not_found,
    assert_unauthenticated,
)
from orders.models import Order

pytestmark = pytest.mark.django_db

NAMES = [
    "overview",
    "products",
    "product-detail",
    "content",
    "content-detail",
    "external",
    "orders-summary",
    "daily",
]
GET_ONLY = [n for n in NAMES if n != "external"]


@pytest.fixture
def s():
    return build_scenario()


def endpoints(s):
    b = s.business
    return {
        "overview": url(b, "overview/"),
        "products": url(b, "products/"),
        "product-detail": url(b, f"products/{s.p1.pk}/"),
        "content": url(b, "content/"),
        "content-detail": url(b, f"content/post/{s.post.pk}/"),
        "external": url(b, f"content/post/{s.post.pk}/external-metrics/"),
        "orders-summary": url(b, "orders-summary/"),
        "daily": url(b, "daily/"),
    }


@pytest.mark.parametrize("name", NAMES)
def test_unauthenticated_gets_401(s, name):
    assert_unauthenticated(client_for().get(endpoints(s)[name]))


@pytest.mark.parametrize("name", NAMES)
def test_garbage_token_gets_401(s, name):
    assert_unauthenticated(garbage_token_client().get(endpoints(s)[name]))


@pytest.mark.parametrize("name", NAMES)
def test_other_merchant_gets_403(s, name):
    attacker = make_business()
    assert_forbidden(client_for(attacker.user).get(endpoints(s)[name]))


@pytest.mark.parametrize("name", NAMES)
def test_customer_gets_403(s, name):
    assert_forbidden(client_for(make_customer()).get(endpoints(s)[name]))


@pytest.mark.parametrize("name", NAMES)
def test_unknown_business_is_404(s, name):
    path = endpoints(s)[name].replace(f"business/{s.business.pk}/", "business/999999/")
    assert_not_found(client_for(s.business.user).get(path))


def test_ownership_is_checked_before_query_validation(s):
    attacker = make_business()
    r = client_for(attacker.user).get(
        endpoints(s)["overview"], {"date_from": "not-a-date"}
    )
    assert_forbidden(r)


@pytest.mark.parametrize("name", GET_ONLY)
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_dashboard_endpoints_are_read_only(s, name, method):
    before_orders = list(Order.objects.order_by("pk").values_list("pk", "status"))
    before_daily = ProductDailyStats.objects.count()
    r = getattr(client_for(s.business.user), method)(
        endpoints(s)[name], {"views": 999, "status": "delivered"}, format="json"
    )
    assert r.status_code == 405
    assert list(Order.objects.order_by("pk").values_list("pk", "status")) == (
        before_orders
    )
    assert ProductDailyStats.objects.count() == before_daily


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_external_endpoint_only_allows_get_and_post(s, method):
    r = getattr(client_for(s.business.user), method)(
        endpoints(s)["external"], {}, format="json"
    )
    assert r.status_code == 405
    assert ExternalContentMetricSnapshot.objects.count() == 0


def test_business_id_in_query_is_ignored(s):
    other = make_business()
    other_product = make_product(other)
    make_order(
        other, other_product, make_customer(), "confirmed", 1, "999.00", at(DAY1)
    )
    body = (
        client_for(s.business.user)
        .get(endpoints(s)["overview"], dict(RANGE, business_id=other.pk))
        .json()
    )
    assert body["business_id"] == s.business.pk
    assert body["overview"]["orders"] == 5
    assert body["overview"]["sales"][0]["sales_value"] == "450.00"


def test_owner_of_a_cannot_read_business_b_by_url(s):
    other = make_business()
    r = client_for(s.business.user).get(url(other, "overview/"))
    assert_forbidden(r)


def test_foreign_product_and_content_ids_are_404_inside_own_business(s):
    attacker = make_business()
    client = client_for(attacker.user)
    assert_not_found(client.get(url(attacker, f"products/{s.p1.pk}/")))
    assert_not_found(client.get(url(attacker, f"content/post/{s.post.pk}/")))
    assert_not_found(
        client.get(url(attacker, f"content/post/{s.post.pk}/external-metrics/"))
    )


def test_each_merchant_only_sees_their_own_numbers(s):
    other = make_business()
    product = make_product(other)
    make_order(other, product, make_customer(), "confirmed", 3, "10.00", at(DAY1))
    mine = client_for(s.business.user).get(endpoints(s)["orders-summary"], RANGE).json()
    theirs = client_for(other.user).get(url(other, "orders-summary/"), RANGE).json()
    assert mine["orders"] == 5
    assert theirs["orders"] == 1 and theirs["units"] == 3
    assert make_post(other)  # sanity: other tenant content never leaks below
    content_ids = {
        r["content_id"]
        for r in client_for(other.user)
        .get(url(other, "content/"), RANGE)
        .json()["results"]
    }
    assert s.post.pk not in content_ids


# ---- BE-06: Subscription Gate on the owner dashboard -----------------
GATED = list(NAMES)


@pytest.mark.parametrize("name", GATED)
def test_locked_owner_gets_subscription_required(s, name):
    give_subscription(s.business, "locked")
    r = client_for(s.business.user).get(endpoints(s)[name])
    assert_error_envelope(r, 403, "SUBSCRIPTION_REQUIRED")


@pytest.mark.parametrize("name", GATED)
def test_owner_with_running_trial_is_not_gated(s, name):
    give_subscription(s.business, "trialing", trial_days=5)
    r = client_for(s.business.user).get(endpoints(s)[name])
    assert r.status_code != 403
