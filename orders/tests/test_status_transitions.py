"""
PART BI-01 - order status transitions (merchant only).

Asserted table (the unambiguous core of the allowed-transitions dict):
  pending    -> confirmed | cancelled
  confirmed  -> processing | cancelled
  processing -> shipped
  shipped    -> delivered
Everything that skips a step, goes backwards or leaves a terminal state
(delivered, cancelled) must be rejected with 400 and change nothing.
"""

import pytest

from orders.models import Order, OrderEvent
from orders.tests import factories
from orders.tests.urls_map import (
    MERCHANT_EVENTS,
    MERCHANT_STATUS,
    STATUS_FIELD,
    STATUS_METHOD,
    url,
)

pytestmark = pytest.mark.django_db

ALLOWED = [
    ("pending", "confirmed"),
    ("confirmed", "processing"),
    ("processing", "shipped"),
    ("shipped", "delivered"),
    ("pending", "cancelled"),
    ("confirmed", "cancelled"),
]

REJECTED = [
    ("cancelled", "delivered"),
    ("cancelled", "confirmed"),
    ("cancelled", "pending"),
    ("delivered", "pending"),
    ("delivered", "cancelled"),
    ("pending", "shipped"),
    ("pending", "delivered"),
    ("confirmed", "delivered"),
    ("processing", "pending"),
    ("shipped", "confirmed"),
]


def _change_status(user, order, new_status):
    client = factories.client_for(user)
    return getattr(client, STATUS_METHOD)(
        url(MERCHANT_STATUS, order_id=order.pk),
        {STATUS_FIELD: new_status},
        format="json",
    )


@pytest.mark.parametrize("old,new", ALLOWED)
def test_allowed_transition_is_applied_and_logged(
    merchant, customer, product, old, new
):
    order = factories.make_order(merchant, customer, product, status=old)

    response = _change_status(merchant.user, order, new)

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.status == new
    event = OrderEvent.objects.get(order=order)
    assert event.from_status == old
    assert event.to_status == new
    assert event.actor_id == merchant.user_id


@pytest.mark.parametrize("old,new", REJECTED)
def test_rejected_transition_changes_nothing(merchant, customer, product, old, new):
    order = factories.make_order(merchant, customer, product, status=old)

    response = _change_status(merchant.user, order, new)

    assert response.status_code == 400
    order.refresh_from_db()
    assert order.status == old
    assert OrderEvent.objects.filter(order=order).count() == 0


def test_unknown_status_value_is_rejected(merchant, customer, product):
    order = factories.make_order(merchant, customer, product)

    response = _change_status(merchant.user, order, "teleported")

    assert response.status_code == 400
    order.refresh_from_db()
    assert order.status == "pending"


def test_missing_status_is_rejected(merchant, customer, product):
    order = factories.make_order(merchant, customer, product)
    client = factories.client_for(merchant.user)

    response = getattr(client, STATUS_METHOD)(
        url(MERCHANT_STATUS, order_id=order.pk), {}, format="json"
    )

    assert response.status_code == 400


def test_lifecycle_timestamps_are_stamped(merchant, customer, product):
    order = factories.make_order(merchant, customer, product)
    assert order.confirmed_at is None
    assert order.completed_at is None
    assert order.cancelled_at is None

    for new_status in ("confirmed", "processing", "shipped", "delivered"):
        assert _change_status(merchant.user, order, new_status).status_code == 200

    order.refresh_from_db()
    assert order.status == "delivered"
    assert order.confirmed_at is not None
    assert order.completed_at is not None
    assert order.cancelled_at is None


def test_cancel_stamps_cancelled_at_only(merchant, customer, product):
    order = factories.make_order(merchant, customer, product)

    assert _change_status(merchant.user, order, "cancelled").status_code == 200

    order.refresh_from_db()
    assert order.cancelled_at is not None
    assert order.confirmed_at is None
    assert order.completed_at is None


def test_events_are_appended_in_order_never_replaced(merchant, customer, product):
    order = factories.make_order(merchant, customer, product)

    for new_status in ("confirmed", "processing", "shipped"):
        _change_status(merchant.user, order, new_status)

    pairs = list(
        OrderEvent.objects.filter(order=order)
        .order_by("created_at", "pk")
        .values_list("from_status", "to_status")
    )
    assert pairs == [
        ("pending", "confirmed"),
        ("confirmed", "processing"),
        ("processing", "shipped"),
    ]
    events = factories.client_for(merchant.user).get(
        url(MERCHANT_EVENTS, order_id=order.pk)
    )
    assert events.status_code == 200
    assert len(factories.results(events)) == 3


def test_customer_cannot_change_status(merchant, customer, product):
    order = factories.make_order(merchant, customer, product)

    response = _change_status(customer, order, "confirmed")

    assert response.status_code == 403
    order.refresh_from_db()
    assert order.status == "pending"


def test_other_merchant_cannot_change_status(
    merchant, other_merchant, customer, product
):
    order = factories.make_order(merchant, customer, product)

    response = _change_status(other_merchant.user, order, "confirmed")

    assert response.status_code in (403, 404)
    order.refresh_from_db()
    assert order.status == "pending"
    assert Order.objects.filter(status="confirmed").count() == 0
