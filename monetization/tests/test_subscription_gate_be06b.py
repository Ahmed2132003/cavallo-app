"""BE-06b: Gate on orders/analytics, hiding in search + public page, payments open."""

import pytest

from businesses.models import BusinessProfile
from core.tests.sweep_factories import client_for, make_business, make_user
from core.tests.sweep_helpers import assert_error_envelope
from monetization.tests.helpers import STATE_CASES, give_subscription
from products.models import Product
from search.services import (
    SearchFilters,
    build_business_queryset,
    build_product_queryset,
)

pytestmark = pytest.mark.django_db

CASE_IDS = [c[0] for c in STATE_CASES]


def _locked_business():
    business = make_business()
    give_subscription(business, "locked")
    return business


def _urls(business):
    return [
        "/api/v1/orders/",
        f"/api/v1/analytics/business/{business.pk}/overview/",
        f"/api/v1/analytics/business/{business.pk}/products/",
        f"/api/v1/analytics/business/{business.pk}/content/",
        f"/api/v1/analytics/business/{business.pk}/orders-summary/",
    ]


@pytest.mark.parametrize("index", range(5))
@pytest.mark.parametrize("case", STATE_CASES, ids=CASE_IDS)
def test_orders_and_analytics_gate_only_locked_merchants(index, case):
    _, status, trial_days, period_days, locked = case
    business = make_business()
    give_subscription(business, status, trial_days, period_days)

    response = client_for(business.user).get(_urls(business)[index])

    if locked:
        assert_error_envelope(response, 403, "SUBSCRIPTION_REQUIRED")
    else:
        assert response.status_code != 403


def test_locked_merchant_can_write_nothing_to_orders():
    business = _locked_business()

    response = client_for(business.user).post(
        "/api/v1/orders/from-chat/", {}, format="json"
    )

    assert_error_envelope(response, 403, "SUBSCRIPTION_REQUIRED")


def test_customer_orders_list_is_not_gated():
    response = client_for(make_user()).get("/api/v1/orders/my/")

    assert response.status_code == 200


def test_view_event_endpoint_is_not_gated_for_a_locked_merchant():
    business = _locked_business()

    response = client_for(business.user).post(
        "/api/v1/analytics/events/views/", {}, format="json"
    )

    # Validation error is fine; the gate must not answer.
    body = response.json()
    assert body.get("error", {}).get("code") != "SUBSCRIPTION_REQUIRED"


def test_locked_merchant_can_reach_payments():
    business = _locked_business()

    response = client_for(business.user).post(
        "/api/v1/payments/initiate/", {}, format="json"
    )

    body = response.json()
    assert body.get("error", {}).get("code") != "SUBSCRIPTION_REQUIRED"


def test_search_hides_locked_businesses_and_their_products():
    locked = _locked_business()
    open_business = make_business()
    give_subscription(open_business, "trialing", trial_days=5)

    business_ids = set(
        build_business_queryset(SearchFilters()).values_list("pk", flat=True)
    )
    product_business_ids = set(
        build_product_queryset(SearchFilters()).values_list("business_id", flat=True)
    )

    assert locked.pk not in business_ids
    assert open_business.pk in business_ids
    assert locked.pk not in product_business_ids
    assert BusinessProfile.objects.filter(pk=locked.pk).exists()
    assert Product.objects.filter(business=locked).count() == 0 or (
        locked.pk not in product_business_ids
    )


def test_public_business_page_is_hidden_when_locked():
    business = _locked_business()

    response = client_for().get(f"/api/v1/businesses/{business.pk}/")

    assert_error_envelope(response, 404, "NOT_FOUND")


def test_public_business_page_stays_visible_when_open():
    business = make_business()
    give_subscription(business, "active", period_days=10)

    response = client_for().get(f"/api/v1/businesses/{business.pk}/")

    assert response.status_code == 200