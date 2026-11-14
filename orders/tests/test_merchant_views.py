"""
PART BI-01 - merchant order management (owner-only, object-level checks).
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from orders.models import Order, OrderEvent
from orders.tests import factories
from orders.tests.urls_map import (
    MERCHANT_DETAIL,
    MERCHANT_EVENTS,
    MERCHANT_LIST,
    MERCHANT_NOTES,
    NOTES_METHOD,
    NOTE_FIELD,
    NOTE_INTERNAL_FIELD,
    PRODUCT_ORDERS,
    url,
)

pytestmark = pytest.mark.django_db


def _ids(response):
    return {row["id"] for row in factories.results(response)}


def _get(user, path, **params):
    return factories.client_for(user).get(path, params)


def test_list_returns_only_the_merchants_own_orders(
    merchant, other_merchant, customer, other_customer, product, other_product
):
    mine = factories.make_order(merchant, customer, product)
    theirs = factories.make_order(other_merchant, other_customer, other_product)

    response = _get(merchant.user, MERCHANT_LIST)

    assert response.status_code == 200
    assert _ids(response) == {mine.pk}
    assert theirs.pk not in _ids(response)


def test_list_is_paginated_like_the_rest_of_the_project(merchant, customer, product):
    factories.make_order(merchant, customer, product)

    response = _get(merchant.user, MERCHANT_LIST)

    assert {"results", "next", "previous"} <= set(response.json())


def test_list_filters_by_status(merchant, customer, product):
    pending = factories.make_order(merchant, customer, product, status="pending")
    confirmed = factories.make_order(merchant, customer, product, status="confirmed")

    response = _get(merchant.user, MERCHANT_LIST, status="confirmed")

    assert _ids(response) == {confirmed.pk}
    assert pending.pk not in _ids(response)


def test_list_filters_by_product(merchant, customer, product):
    second_product = factories.make_product(merchant)
    first = factories.make_order(merchant, customer, product)
    second = factories.make_order(merchant, customer, second_product)

    response = _get(merchant.user, MERCHANT_LIST, product=second_product.pk)

    assert _ids(response) == {second.pk}
    assert first.pk not in _ids(response)


def test_list_filters_by_customer(merchant, customer, other_customer, product):
    first = factories.make_order(merchant, customer, product)
    second = factories.make_order(merchant, other_customer, product)

    response = _get(merchant.user, MERCHANT_LIST, customer=other_customer.pk)

    assert _ids(response) == {second.pk}
    assert first.pk not in _ids(response)


def test_list_filters_by_date_range(merchant, customer, product):
    old = factories.make_order(merchant, customer, product)
    recent = factories.make_order(merchant, customer, product)
    old_date = timezone.now() - timedelta(days=400)
    Order.objects.filter(pk=old.pk).update(created_at=old_date)

    only_old = _get(
        merchant.user,
        MERCHANT_LIST,
        date_from=(old_date - timedelta(days=1)).date().isoformat(),
        date_to=(old_date + timedelta(days=1)).date().isoformat(),
    )
    only_recent = _get(
        merchant.user,
        MERCHANT_LIST,
        date_from=(timezone.now() - timedelta(days=1)).date().isoformat(),
    )

    assert _ids(only_old) == {old.pk}
    assert _ids(only_recent) == {recent.pk}


def test_list_searches_by_order_number(merchant, customer, product):
    first = factories.make_order(merchant, customer, product)
    second = factories.make_order(merchant, customer, product)

    response = _get(merchant.user, MERCHANT_LIST, search=str(second.pk))

    assert second.pk in _ids(response)
    assert first.pk not in _ids(response)


def test_detail_shows_internal_note_to_the_merchant(merchant, customer, product):
    order = factories.make_order(
        merchant,
        customer,
        product,
        customer_note="visible to both",
        internal_note="merchant eyes only",
    )

    response = _get(merchant.user, url(MERCHANT_DETAIL, order_id=order.pk))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == order.pk
    assert body["customer_note"] == "visible to both"
    assert body["internal_note"] == "merchant eyes only"


def test_other_merchant_cannot_read_detail_or_events(
    merchant, other_merchant, customer, product
):
    order = factories.make_order(merchant, customer, product)

    detail = _get(other_merchant.user, url(MERCHANT_DETAIL, order_id=order.pk))
    events = _get(other_merchant.user, url(MERCHANT_EVENTS, order_id=order.pk))

    assert detail.status_code in (403, 404)
    assert events.status_code in (403, 404)


def test_customer_cannot_use_merchant_endpoints(merchant, customer, product):
    order = factories.make_order(merchant, customer, product)

    listing = _get(customer, MERCHANT_LIST)
    detail = _get(customer, url(MERCHANT_DETAIL, order_id=order.pk))

    assert listing.status_code == 403
    assert detail.status_code in (403, 404)


def test_merchant_endpoints_require_authentication(merchant, customer, product):
    order = factories.make_order(merchant, customer, product)

    listing = APIClient().get(MERCHANT_LIST)
    detail = APIClient().get(url(MERCHANT_DETAIL, order_id=order.pk))

    assert listing.status_code == 401
    assert detail.status_code == 401


def _add_note(user, order, text, is_internal):
    client = factories.client_for(user)
    return getattr(client, NOTES_METHOD)(
        url(MERCHANT_NOTES, order_id=order.pk),
        {NOTE_FIELD: text, NOTE_INTERNAL_FIELD: is_internal},
        format="json",
    )


def test_merchant_adds_internal_and_visible_notes(merchant, customer, product):
    order = factories.make_order(merchant, customer, product)

    internal = _add_note(merchant.user, order, "internal remark", True)
    visible = _add_note(merchant.user, order, "visible remark", False)

    assert internal.status_code in (200, 201)
    assert visible.status_code in (200, 201)
    flags = dict(
        OrderEvent.objects.filter(order=order).values_list("note", "is_internal")
    )
    assert flags == {"internal remark": True, "visible remark": False}


def test_merchant_sees_every_event_including_internal(merchant, customer, product):
    order = factories.make_order(merchant, customer, product)
    factories.make_event(order, merchant.user, note="seen internal", is_internal=True)
    factories.make_event(order, merchant.user, note="seen public", is_internal=False)

    response = _get(merchant.user, url(MERCHANT_EVENTS, order_id=order.pk))

    assert response.status_code == 200
    notes = {row["note"] for row in factories.results(response)}
    assert notes == {"seen internal", "seen public"}


def test_other_merchant_cannot_add_notes(merchant, other_merchant, customer, product):
    order = factories.make_order(merchant, customer, product)

    response = _add_note(other_merchant.user, order, "intruder", False)

    assert response.status_code in (403, 404)
    assert OrderEvent.objects.filter(order=order).count() == 0


def test_customer_cannot_add_notes_through_merchant_endpoint(
    merchant, customer, product
):
    order = factories.make_order(merchant, customer, product)

    response = _add_note(customer, order, "sneaky", False)

    assert response.status_code in (403, 404)
    assert OrderEvent.objects.filter(order=order).count() == 0


def test_product_orders_lists_only_that_products_orders(merchant, customer, product):
    other_own_product = factories.make_product(merchant)
    wanted = factories.make_order(merchant, customer, product)
    unwanted = factories.make_order(merchant, customer, other_own_product)

    response = _get(merchant.user, url(PRODUCT_ORDERS, product_id=product.pk))

    assert response.status_code == 200
    assert _ids(response) == {wanted.pk}
    assert unwanted.pk not in _ids(response)


def test_product_orders_is_closed_to_other_merchants_and_customers(
    merchant, other_merchant, customer, product
):
    factories.make_order(merchant, customer, product)
    path = url(PRODUCT_ORDERS, product_id=product.pk)

    from_other_merchant = _get(other_merchant.user, path)
    from_customer = _get(customer, path)

    assert from_customer.status_code in (403, 404)
    assert from_other_merchant.status_code in (200, 403, 404)
    if from_other_merchant.status_code == 200:
        assert factories.results(from_other_merchant) == []
