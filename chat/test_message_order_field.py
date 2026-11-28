"""
PART BI-01 - the additive `order` field on MessageSerializer.

`order` is null when no order was recorded from the message. Nothing that
existed before may change: old keys, status, shared_content, and the
context-free serializer call the WebSocket broadcast makes.
"""

from unittest import mock

import pytest
from django.urls import reverse

from chat.models import Message
from chat.serializers import MessageSerializer
from orders.tests import factories

pytestmark = pytest.mark.django_db

LEGACY_KEYS = {
    "id",
    "conversation",
    "sender",
    "text",
    "media",
    "media_type",
    "shared_content",
    "status",
    "created_at",
}
INTERNAL_MARKER = "MSG-INTERNAL-MARKER-5c1d"


@pytest.fixture(autouse=True)
def _no_real_broadcast_or_push():
    with mock.patch("chat.views.notify_offline_recipient"), mock.patch(
        "chat.views.get_channel_layer"
    ), mock.patch("chat.views.async_to_sync"):
        yield


def _history(user, conversation):
    return factories.client_for(user).get(
        reverse(
            "chat:conversation-messages", kwargs={"conversation_id": conversation.pk}
        )
    )


def _by_id(rows):
    return {row["id"]: row for row in rows}


def test_message_without_order_has_null_order(customer, conversation, product_message):
    response = _history(customer, conversation)

    assert response.status_code == 200
    row = _by_id(factories.results(response))[product_message.pk]
    assert "order" in row
    assert row["order"] is None


def test_message_with_order_exposes_it(
    merchant, customer, product, conversation, product_message
):
    order = factories.make_order(
        merchant,
        customer,
        product,
        conversation=conversation,
        message=product_message,
    )

    response = _history(merchant.user, conversation)

    row = _by_id(factories.results(response))[product_message.pk]
    assert row["order"] is not None
    assert factories.order_id_of(row["order"]) == order.pk


def test_only_the_linked_message_carries_an_order(
    merchant, customer, product, conversation, product_message
):
    plain = factories.make_message(conversation, customer, text="thanks")
    factories.make_order(
        merchant,
        customer,
        product,
        conversation=conversation,
        message=product_message,
    )

    rows = _by_id(factories.results(_history(customer, conversation)))

    assert rows[plain.pk]["order"] is None
    assert rows[product_message.pk]["order"] is not None


def test_legacy_keys_and_shared_content_are_unchanged(
    merchant, customer, product, conversation, product_message
):
    factories.make_order(
        merchant,
        customer,
        product,
        conversation=conversation,
        message=product_message,
    )

    row = _by_id(factories.results(_history(customer, conversation)))[
        product_message.pk
    ]

    assert LEGACY_KEYS <= set(row)
    assert row["status"] == "sent"
    assert row["shared_content"]["content_type"] == "product"
    assert row["shared_content"]["object_id"] == product.pk
    assert row["shared_content"]["available"] is True


def test_creating_an_order_does_not_touch_message_status(
    merchant, customer, product, conversation, product_message
):
    factories.make_order(
        merchant,
        customer,
        product,
        conversation=conversation,
        message=product_message,
    )

    product_message.refresh_from_db()

    assert product_message.status == Message.Status.SENT


def test_fetch_since_endpoint_includes_the_order_field(
    merchant, customer, product, conversation, product_message
):
    order = factories.make_order(
        merchant,
        customer,
        product,
        conversation=conversation,
        message=product_message,
    )

    response = factories.client_for(customer).get(
        reverse(
            "chat:conversation-messages", kwargs={"conversation_id": conversation.pk}
        ),
        {"since": 0},
    )

    assert response.status_code == 200
    row = _by_id(response.json())[product_message.pk]
    assert factories.order_id_of(row["order"]) == order.pk


def test_new_message_response_has_null_order(customer, conversation):
    response = factories.client_for(customer).post(
        reverse(
            "chat:conversation-messages", kwargs={"conversation_id": conversation.pk}
        ),
        {"text": "hello"},
        format="json",
    )

    assert response.status_code == 201
    body = response.json()
    assert LEGACY_KEYS <= set(body)
    assert body["order"] is None


def test_customer_never_sees_internal_note_through_messages(
    merchant, customer, product, conversation, product_message
):
    factories.make_order(
        merchant,
        customer,
        product,
        conversation=conversation,
        message=product_message,
        internal_note=INTERNAL_MARKER,
    )

    response = _history(customer, conversation)

    assert response.status_code == 200
    assert INTERNAL_MARKER not in response.content.decode()


def test_serializer_works_without_request_context_like_the_websocket_path(
    merchant, customer, product, conversation, product_message
):
    without_order = MessageSerializer(product_message).data
    assert "order" in without_order
    assert without_order["order"] is None

    factories.make_order(
        merchant,
        customer,
        product,
        conversation=conversation,
        message=product_message,
        internal_note=INTERNAL_MARKER,
    )
    with_order = MessageSerializer(Message.objects.get(pk=product_message.pk)).data

    assert with_order["order"] is not None
    assert INTERNAL_MARKER not in str(with_order)


def test_non_participant_still_gets_403(other_customer, conversation):
    response = _history(other_customer, conversation)

    assert response.status_code == 403
