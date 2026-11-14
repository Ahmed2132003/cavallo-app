"""
PART BI-01 - POST /api/v1/orders/from-chat/ (merchant records a sale).

The server derives merchant, customer, product and currency; nothing about
identity is trusted from the request body.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, connections, transaction
from rest_framework.test import APIClient

from orders.models import Order, OrderEvent
from orders.tests import factories
from orders.tests.urls_map import FROM_CHAT, MERCHANT_DETAIL, url

pytestmark = pytest.mark.django_db


def _post(user, payload):
    return factories.client_for(user).post(FROM_CHAT, payload, format="json")


def test_merchant_creates_order_from_chat(
    merchant, customer, product, conversation, product_message
):
    payload = factories.from_chat_payload(conversation, product_message)

    response = _post(merchant.user, payload)

    assert response.status_code == 201
    order = Order.objects.get(pk=response.json()["id"])
    assert order.business_id == merchant.id
    assert order.customer_id == customer.id
    assert order.product_id == product.id
    assert order.conversation_id == conversation.id
    assert order.source_message_id == product_message.id
    assert order.quantity == 2
    assert order.agreed_unit_price == Decimal("100.00")
    assert order.total_amount == Decimal("200.00")
    assert order.currency == "EGP"
    assert order.status == "pending"
    assert order.customer_note == "customer note"
    assert order.internal_note == "internal note"


def test_first_order_event_is_recorded_on_creation(
    merchant, conversation, product_message
):
    payload = factories.from_chat_payload(conversation, product_message)

    response = _post(merchant.user, payload)

    order = Order.objects.get(pk=response.json()["id"])
    events = OrderEvent.objects.filter(order=order)
    assert events.count() == 1
    event = events.get()
    assert event.actor_id == merchant.user_id
    assert event.to_status == "pending"


def test_total_amount_is_quantity_times_agreed_price(
    merchant, conversation, product_message
):
    payload = factories.from_chat_payload(
        conversation, product_message, quantity=3, agreed_unit_price="125.50"
    )

    response = _post(merchant.user, payload)

    assert response.status_code == 201
    assert factories.dec(response.json()["total_amount"]) == Decimal("376.50")
    order = Order.objects.get(pk=response.json()["id"])
    assert order.total_amount == Decimal("376.50")


def test_client_supplied_identity_and_amounts_are_ignored(
    merchant,
    other_merchant,
    customer,
    other_customer,
    product,
    other_product,
    conversation,
    product_message,
):
    payload = factories.from_chat_payload(
        conversation,
        product_message,
        business=other_merchant.id,
        business_id=other_merchant.id,
        customer=other_customer.id,
        customer_id=other_customer.id,
        product=other_product.id,
        product_id=other_product.id,
        currency="SAR",
        total_amount="1.00",
        status="delivered",
    )

    response = _post(merchant.user, payload)

    assert response.status_code == 201
    order = Order.objects.get(pk=response.json()["id"])
    assert order.business_id == merchant.id
    assert order.customer_id == customer.id
    assert order.product_id == product.id
    assert order.currency == "EGP"
    assert order.total_amount == Decimal("200.00")
    assert order.status == "pending"


def test_unauthenticated_request_is_rejected(conversation, product_message):
    payload = factories.from_chat_payload(conversation, product_message)

    response = APIClient().post(FROM_CHAT, payload, format="json")

    assert response.status_code == 401
    assert Order.objects.count() == 0


def test_customer_cannot_create_order(customer, conversation, product_message):
    payload = factories.from_chat_payload(conversation, product_message)

    response = _post(customer, payload)

    assert response.status_code == 403
    assert Order.objects.count() == 0


def test_other_merchant_cannot_create_order(
    other_merchant, conversation, product_message
):
    payload = factories.from_chat_payload(conversation, product_message)

    response = _post(other_merchant.user, payload)

    assert response.status_code == 403
    assert Order.objects.count() == 0


def test_product_of_a_different_merchant_is_rejected(
    merchant, customer, other_product, conversation
):
    message = factories.make_product_message(conversation, customer, other_product)
    payload = factories.from_chat_payload(conversation, message)

    response = _post(merchant.user, payload)

    assert response.status_code in (400, 403, 404)
    assert Order.objects.count() == 0


def test_message_without_a_product_is_rejected(merchant, customer, conversation):
    message = factories.make_message(conversation, customer, text="how much?")
    payload = factories.from_chat_payload(conversation, message)

    response = _post(merchant.user, payload)

    assert response.status_code == 400
    assert Order.objects.count() == 0


def test_message_from_another_conversation_is_rejected(
    merchant, other_customer, product, conversation
):
    other_conversation = factories.make_conversation(merchant.user, other_customer)
    foreign_message = factories.make_product_message(
        other_conversation, other_customer, product
    )
    payload = factories.from_chat_payload(conversation, foreign_message)

    response = _post(merchant.user, payload)

    assert response.status_code in (400, 404)
    assert Order.objects.count() == 0


def test_conversation_with_another_business_is_rejected(
    merchant, other_merchant, product
):
    conversation = factories.make_conversation(merchant.user, other_merchant.user)
    message = factories.make_product_message(conversation, other_merchant.user, product)
    payload = factories.from_chat_payload(conversation, message)

    response = _post(merchant.user, payload)

    assert response.status_code in (400, 403)
    assert Order.objects.count() == 0


def test_soft_deleted_product_is_rejected(
    merchant, product, conversation, product_message
):
    product.delete()
    payload = factories.from_chat_payload(conversation, product_message)

    response = _post(merchant.user, payload)

    assert response.status_code in (400, 404)
    assert Order.objects.count() == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"quantity": 0},
        {"quantity": -1},
        {"agreed_unit_price": "-5.00"},
        {"agreed_unit_price": "not-a-number"},
    ],
)
def test_invalid_quantity_or_price_is_rejected(
    merchant, conversation, product_message, overrides
):
    payload = factories.from_chat_payload(conversation, product_message, **overrides)

    response = _post(merchant.user, payload)

    assert response.status_code == 400
    assert Order.objects.count() == 0


@pytest.mark.parametrize("missing", ["quantity", "agreed_unit_price"])
def test_missing_required_field_is_rejected(
    merchant, conversation, product_message, missing
):
    payload = factories.from_chat_payload(conversation, product_message)
    payload.pop(missing)

    response = _post(merchant.user, payload)

    assert response.status_code == 400
    assert Order.objects.count() == 0


def test_duplicate_request_returns_the_same_order(
    merchant, conversation, product_message
):
    payload = factories.from_chat_payload(conversation, product_message)

    first = _post(merchant.user, payload)
    changed = dict(payload, quantity=9, agreed_unit_price="1.00")
    second = _post(merchant.user, changed)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert Order.objects.count() == 1
    assert OrderEvent.objects.count() == 1
    order = Order.objects.get()
    assert order.quantity == 2
    assert order.agreed_unit_price == Decimal("100.00")


def test_source_message_is_unique_at_database_level(
    merchant, customer, product, conversation, product_message
):
    factories.make_order(
        merchant,
        customer,
        product,
        conversation=conversation,
        message=product_message,
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            factories.make_order(
                merchant,
                customer,
                product,
                conversation=conversation,
                message=product_message,
            )

    assert Order.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_requests_create_exactly_one_order(
    merchant, conversation, product_message
):
    if connection.vendor != "postgresql":
        pytest.skip("select_for_update race needs PostgreSQL.")

    payload = factories.from_chat_payload(conversation, product_message)
    barrier = threading.Barrier(2)

    def worker():
        try:
            barrier.wait(timeout=10)
            return _post(merchant.user, payload).status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        statuses = sorted(future.result(timeout=60) for future in futures)

    assert statuses == [200, 201]
    assert Order.objects.filter(source_message=product_message).count() == 1
    assert OrderEvent.objects.count() == 1


def test_price_and_currency_are_a_snapshot(
    merchant, customer, product, conversation, product_message
):
    payload = factories.from_chat_payload(
        conversation, product_message, quantity=2, agreed_unit_price="100.00"
    )
    created = _post(merchant.user, payload)
    order_id = created.json()["id"]

    product.price = Decimal("999.00")
    product.currency = "SAR"
    product.save()

    detail = factories.client_for(merchant.user).get(
        url(MERCHANT_DETAIL, order_id=order_id)
    )

    assert detail.status_code == 200
    body = detail.json()
    assert factories.dec(body["agreed_unit_price"]) == Decimal("100.00")
    assert factories.dec(body["total_amount"]) == Decimal("200.00")
    assert body["currency"] == "EGP"
    order = Order.objects.get(pk=order_id)
    assert order.agreed_unit_price == Decimal("100.00")
    assert order.currency == "EGP"


def test_existing_order_stays_readable_after_product_is_deleted(
    merchant, customer, product
):
    order = factories.make_order(merchant, customer, product)
    product.delete()

    response = factories.client_for(merchant.user).get(
        url(MERCHANT_DETAIL, order_id=order.pk)
    )

    assert response.status_code == 200
    assert response.json()["id"] == order.pk
