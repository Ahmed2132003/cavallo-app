"""
PART BI-01 - customer-side order views.

The customer never sees `internal_note` nor any event flagged `is_internal`.
Every leak check scans the raw response body for a unique marker so it holds
no matter which serializer field would have carried the text.
"""

import pytest
from rest_framework.test import APIClient

from orders.tests import factories
from orders.tests.urls_map import (
    CUSTOMER_DETAIL,
    CUSTOMER_EVENTS,
    CUSTOMER_LIST,
    MERCHANT_DETAIL,
    url,
)

pytestmark = pytest.mark.django_db

INTERNAL_NOTE_MARKER = "INTERNAL-NOTE-MARKER-7f3a"
INTERNAL_EVENT_MARKER = "INTERNAL-EVENT-MARKER-91bc"
VISIBLE_EVENT_MARKER = "VISIBLE-EVENT-MARKER-22de"


def _get(user, path, **params):
    return factories.client_for(user).get(path, params)


def _order_with_events(merchant, customer, product):
    order = factories.make_order(
        merchant,
        customer,
        product,
        customer_note="customer wrote this",
        internal_note=INTERNAL_NOTE_MARKER,
    )
    factories.make_event(
        order, merchant.user, note=INTERNAL_EVENT_MARKER, is_internal=True
    )
    factories.make_event(
        order, merchant.user, note=VISIBLE_EVENT_MARKER, is_internal=False
    )
    return order


def test_customer_lists_only_their_own_orders(
    merchant, customer, other_customer, product
):
    mine = factories.make_order(merchant, customer, product)
    theirs = factories.make_order(merchant, other_customer, product)

    response = _get(customer, CUSTOMER_LIST)

    assert response.status_code == 200
    ids = {row["id"] for row in factories.results(response)}
    assert ids == {mine.pk}
    assert theirs.pk not in ids


def test_customer_list_never_contains_internal_note(merchant, customer, product):
    _order_with_events(merchant, customer, product)

    response = _get(customer, CUSTOMER_LIST)

    assert response.status_code == 200
    assert INTERNAL_NOTE_MARKER not in response.content.decode()
    assert INTERNAL_EVENT_MARKER not in response.content.decode()
    for row in factories.results(response):
        assert "internal_note" not in row


def test_customer_detail_shows_customer_note_but_hides_internal_data(
    merchant, customer, product
):
    order = _order_with_events(merchant, customer, product)

    response = _get(customer, url(CUSTOMER_DETAIL, order_id=order.pk))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == order.pk
    assert body["customer_note"] == "customer wrote this"
    assert "internal_note" not in body
    text = response.content.decode()
    assert INTERNAL_NOTE_MARKER not in text
    assert INTERNAL_EVENT_MARKER not in text


def test_customer_events_exclude_internal_events(merchant, customer, product):
    if CUSTOMER_EVENTS is None:
        pytest.skip("Events are embedded in the customer detail payload.")
    order = _order_with_events(merchant, customer, product)

    response = _get(customer, url(CUSTOMER_EVENTS, order_id=order.pk))

    assert response.status_code == 200
    text = response.content.decode()
    assert VISIBLE_EVENT_MARKER in text
    assert INTERNAL_EVENT_MARKER not in text
    assert INTERNAL_NOTE_MARKER not in text
    for row in factories.results(response):
        assert not row.get("is_internal", False)


def test_merchant_still_sees_internal_data_of_the_same_order(
    merchant, customer, product
):
    order = _order_with_events(merchant, customer, product)

    response = _get(merchant.user, url(MERCHANT_DETAIL, order_id=order.pk))

    assert response.status_code == 200
    assert response.json()["internal_note"] == INTERNAL_NOTE_MARKER


def test_other_customer_cannot_read_someone_elses_order(
    merchant, customer, other_customer, product
):
    order = _order_with_events(merchant, customer, product)

    response = _get(other_customer, url(CUSTOMER_DETAIL, order_id=order.pk))

    assert response.status_code in (403, 404)
    assert INTERNAL_NOTE_MARKER not in response.content.decode()


def test_other_customer_cannot_read_events_of_someone_elses_order(
    merchant, customer, other_customer, product
):
    if CUSTOMER_EVENTS is None:
        pytest.skip("Events are embedded in the customer detail payload.")
    order = _order_with_events(merchant, customer, product)

    response = _get(other_customer, url(CUSTOMER_EVENTS, order_id=order.pk))

    assert response.status_code in (403, 404)
    assert VISIBLE_EVENT_MARKER not in response.content.decode()


def test_merchant_cannot_use_customer_endpoints(merchant, customer, product):
    order = factories.make_order(merchant, customer, product)

    listing = _get(merchant.user, CUSTOMER_LIST)
    detail = _get(merchant.user, url(CUSTOMER_DETAIL, order_id=order.pk))

    # The endpoint filters by customer=request.user, so a merchant sees
    # only their own orders as a buyer (none here) - never the customer's.
    assert listing.status_code == 200
    assert factories.results(listing) == []
    assert detail.status_code in (403, 404)


def test_customer_endpoints_require_authentication(merchant, customer, product):
    order = factories.make_order(merchant, customer, product)

    listing = APIClient().get(CUSTOMER_LIST)
    detail = APIClient().get(url(CUSTOMER_DETAIL, order_id=order.pk))

    assert listing.status_code == 401
    assert detail.status_code == 401


def test_customers_orders_of_different_merchants_are_all_listed(
    merchant, other_merchant, customer, product, other_product
):
    first = factories.make_order(merchant, customer, product)
    second = factories.make_order(other_merchant, customer, other_product)

    response = _get(customer, CUSTOMER_LIST)

    ids = {row["id"] for row in factories.results(response)}
    assert ids == {first.pk, second.pk}
