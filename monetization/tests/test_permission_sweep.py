"""
P-096 (step 3) permission sweep for the monetization app.

The only route is the public, read-only GET /api/v1/monetization/plans/
(authentication_classes = [] on purpose, so the Web Dashboard can show
pricing before login). Activating Featured status is NEVER exposed over
HTTP (monetization.services.activate_subscription is called only by the
payments webhook / reconciliation).

Category 1 (adapted): the route is public by design, so "401" does not
            apply; instead a missing, valid or garbage token all get the
            same 200 with the same body, and the response exposes only
            the five documented Plan fields.
Category 2: no client can create, change or delete a Plan or a
            FeaturedSubscription: write methods are 405 with the P-012
            envelope for anonymous AND signed-in users, no detail route
            exists, and no plausible activation route exists (a business
            owner cannot make themselves Featured over HTTP).
Category 3: no capability-gated route exists in this app (Plans and
            subscriptions are managed in Django Admin only).
BE-03:      GET/POST subscriptions/custom-quote/ is merchant-only (401
            anonymous/garbage token, 403 customer), read-only (no write
            side effects) and rejects every other method with 405.
"""

import pytest

from core.tests.sweep_factories import (
    client_for,
    garbage_token_client,
    make_business,
    make_user,
)
from core.tests.sweep_helpers import assert_error_envelope
from monetization.models import FeaturedSubscription, Plan, Subscription
from payments.tests.factories import make_plan

pytestmark = pytest.mark.django_db

URL = "/api/v1/monetization/plans/"
# Legacy five + BE-01 catalog fields (additive, nothing removed).
PLAN_FIELDS = {
    "id",
    "name",
    "duration_days",
    "price",
    "currency",
    "code",
    "name_ar",
    "name_en",
    "price_egp",
    "storage_bytes",
    "is_featured",
    "trial_days",
    "is_custom",
    "sort_order",
}


@pytest.fixture(autouse=True)
def _no_seeded_plans(db):
    # BE-01's migration seeds the catalog; these tests assert on exact
    # contents and counts, so each starts from an empty Plan table.
    Plan.objects.all().delete()


@pytest.mark.parametrize(
    "client_factory",
    [client_for, garbage_token_client, lambda: client_for(make_user())],
    ids=["anonymous", "garbage-token", "signed-in-customer"],
)
def test_public_list_is_identical_for_every_caller_and_exposes_only_plan_fields(
    client_factory,
):
    make_plan(name="Featured 30", days=30)
    make_plan(name="Featured 7", days=7, price="80.00")

    response = client_factory().get(URL)

    assert response.status_code == 200
    body = response.json()
    assert [plan["duration_days"] for plan in body] == [7, 30]
    for plan in body:
        assert set(plan) == PLAN_FIELDS


@pytest.mark.parametrize("actor", ["anonymous", "business_owner"])
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_plans_cannot_be_created_changed_or_deleted_over_http(actor, method):
    plan = make_plan(name="Original", price="250.00")
    client = client_for() if actor == "anonymous" else client_for(make_business().user)

    response = getattr(client, method)(
        URL, {"name": "Hacked", "price": "0.01", "duration_days": 1}, format="json"
    )

    assert_error_envelope(response, 405, "METHOD_NOT_ALLOWED")
    assert Plan.objects.count() == 1
    plan.refresh_from_db()
    assert plan.name == "Original"
    assert str(plan.price) == "250.00"


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_no_detail_route_exists_for_a_plan(method):
    plan = make_plan()
    client = client_for(make_business().user)

    response = getattr(client, method)(f"{URL}{plan.pk}/")

    assert response.status_code == 404
    assert Plan.objects.filter(pk=plan.pk).exists()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/monetization/",
        "/api/v1/monetization/activate/",
        "/api/v1/monetization/subscribe/",
        "/api/v1/monetization/subscriptions/",
        "/api/v1/monetization/featured/",
    ],
)
def test_a_business_owner_cannot_make_themselves_featured_over_http(path):
    business = make_business()
    plan = make_plan()

    response = client_for(business.user).post(
        path, {"plan_id": plan.pk, "business_id": business.pk}, format="json"
    )

    assert response.status_code in (404, 405)
    assert FeaturedSubscription.objects.count() == 0
    business.refresh_from_db()
    assert business.is_featured is False


QUOTE_URL = "/api/v1/monetization/subscriptions/custom-quote/"


def _quote_call(client, method):
    if method == "get":
        return client.get(QUOTE_URL, {"gb": "20"})
    return client.post(QUOTE_URL, {"gb": 20}, format="json")


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    "client_factory",
    [client_for, garbage_token_client],
    ids=["anonymous", "garbage-token"],
)
def test_custom_quote_requires_authentication(client_factory, method):
    response = _quote_call(client_factory(), method)

    assert response.status_code == 401


@pytest.mark.parametrize("method", ["get", "post"])
def test_custom_quote_is_forbidden_for_a_customer(method):
    response = _quote_call(client_for(make_user()), method)

    assert response.status_code == 403


@pytest.mark.parametrize("method", ["get", "post"])
def test_custom_quote_works_for_a_business_owner(method):
    response = _quote_call(client_for(make_business().user), method)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"gb", "price_egp", "quota_bytes", "is_featured"}
    assert body["price_egp"] == "630.00"


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_custom_quote_rejects_other_methods(method):
    client = client_for(make_business().user)

    response = getattr(client, method)(QUOTE_URL, {"gb": 20}, format="json")

    assert_error_envelope(response, 405, "METHOD_NOT_ALLOWED")


@pytest.mark.parametrize("method", ["get", "post"])
def test_custom_quote_has_no_side_effects(method):
    business = make_business()

    response = _quote_call(client_for(business.user), method)

    assert response.status_code == 200
    assert Subscription.objects.count() == 0
    assert FeaturedSubscription.objects.count() == 0
    business.refresh_from_db()
    assert business.is_featured is False
