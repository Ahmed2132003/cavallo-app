"""
PART BI-01 - ConversationProductInterest.

Recorded when a customer opens a conversation FROM a product
(POST /api/v1/conversations/start/ with product_id) and when a customer
shares a Product into an existing conversation. One row per
(conversation, product); repeats only refresh last_opened_at.
"""

from datetime import timedelta
from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import Conversation, ConversationProductInterest
from orders.tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_real_broadcast_or_push():
    """Message POSTs must not touch Redis / Celery from the test run."""
    with mock.patch("chat.views.notify_offline_recipient"), mock.patch(
        "chat.views.get_channel_layer"
    ), mock.patch("chat.views.async_to_sync"):
        yield


def _start(user, **body):
    return factories.client_for(user).post(
        reverse("chat:conversation-start"), body, format="json"
    )


def _share_product(user, conversation, product):
    return factories.client_for(user).post(
        reverse(
            "chat:conversation-messages", kwargs={"conversation_id": conversation.pk}
        ),
        {"shared_content_type": "product", "shared_object_id": product.pk},
        format="json",
    )


def test_start_with_product_records_interest(merchant, customer, product):
    response = _start(customer, business_id=merchant.id, product_id=product.id)

    assert response.status_code == 201
    interest = ConversationProductInterest.objects.get()
    assert interest.conversation_id == response.json()["id"]
    assert interest.business_id == merchant.id
    assert interest.product_id == product.id
    assert interest.customer_id == customer.id
    assert interest.source
    assert interest.first_opened_at is not None
    assert interest.last_opened_at is not None


def test_start_without_product_still_works_and_records_nothing(merchant, customer):
    response = _start(customer, business_id=merchant.id)

    assert response.status_code == 201
    assert Conversation.objects.count() == 1
    assert ConversationProductInterest.objects.count() == 0


def test_start_with_recipient_id_is_unchanged(merchant, customer):
    response = _start(customer, recipient_id=merchant.user_id)

    assert response.status_code == 201
    assert ConversationProductInterest.objects.count() == 0


def test_start_with_product_of_another_business_is_rejected(
    merchant, other_product, customer
):
    response = _start(customer, business_id=merchant.id, product_id=other_product.id)

    assert response.status_code in (400, 404)
    assert Conversation.objects.count() == 0
    assert ConversationProductInterest.objects.count() == 0


def test_start_with_soft_deleted_product_is_rejected(merchant, product, customer):
    product.delete()

    response = _start(customer, business_id=merchant.id, product_id=product.id)

    assert response.status_code in (400, 404)
    assert Conversation.objects.count() == 0
    assert ConversationProductInterest.objects.count() == 0


def test_start_with_unknown_product_id_is_rejected(merchant, customer):
    response = _start(customer, business_id=merchant.id, product_id=999999)

    assert response.status_code in (400, 404)
    assert Conversation.objects.count() == 0


def test_start_with_non_integer_product_id_is_rejected(merchant, customer):
    response = _start(customer, business_id=merchant.id, product_id="abc")

    assert response.status_code == 400
    assert Conversation.objects.count() == 0


def test_start_requires_authentication(merchant, product):
    response = APIClient().post(
        reverse("chat:conversation-start"),
        {"business_id": merchant.id, "product_id": product.id},
        format="json",
    )

    assert response.status_code == 401


def test_start_in_existing_conversation_reuses_it_and_records_interest(
    merchant, customer, product, conversation
):
    response = _start(customer, business_id=merchant.id, product_id=product.id)

    assert response.status_code == 200
    assert response.json()["id"] == conversation.pk
    assert Conversation.objects.count() == 1
    interest = ConversationProductInterest.objects.get()
    assert interest.conversation_id == conversation.pk
    assert interest.product_id == product.id


def test_repeated_start_does_not_duplicate_interest(merchant, customer, product):
    first = _start(customer, business_id=merchant.id, product_id=product.id)
    interest = ConversationProductInterest.objects.get()
    first_opened = interest.first_opened_at
    long_ago = timezone.now() - timedelta(days=1)
    ConversationProductInterest.objects.filter(pk=interest.pk).update(
        last_opened_at=long_ago
    )

    second = _start(customer, business_id=merchant.id, product_id=product.id)
    third = _start(customer, business_id=merchant.id, product_id=product.id)

    assert first.status_code == 201
    assert second.status_code == 200
    assert third.status_code == 200
    assert Conversation.objects.count() == 1
    assert ConversationProductInterest.objects.count() == 1
    interest.refresh_from_db()
    assert interest.first_opened_at == first_opened
    assert interest.last_opened_at > long_ago


def test_second_product_in_same_conversation_adds_a_second_row(
    merchant, customer, product
):
    other_own_product = factories.make_product(merchant)

    _start(customer, business_id=merchant.id, product_id=product.id)
    _start(customer, business_id=merchant.id, product_id=other_own_product.id)

    assert Conversation.objects.count() == 1
    products = set(
        ConversationProductInterest.objects.values_list("product_id", flat=True)
    )
    assert products == {product.id, other_own_product.id}


def test_sharing_product_in_existing_conversation_records_interest(
    merchant, customer, product, conversation
):
    response = _share_product(customer, conversation, product)

    assert response.status_code == 201
    interest = ConversationProductInterest.objects.get()
    assert interest.conversation_id == conversation.pk
    assert interest.product_id == product.id
    assert interest.business_id == merchant.id
    assert interest.customer_id == customer.id
    assert interest.source


def test_sharing_same_product_twice_does_not_duplicate_interest(
    customer, product, conversation
):
    first = _share_product(customer, conversation, product)
    second = _share_product(customer, conversation, product)

    assert first.status_code == 201
    assert second.status_code == 201
    assert ConversationProductInterest.objects.count() == 1


def test_sharing_after_start_does_not_duplicate_interest(merchant, customer, product):
    started = _start(customer, business_id=merchant.id, product_id=product.id)
    conversation = Conversation.objects.get(pk=started.json()["id"])

    shared = _share_product(customer, conversation, product)

    assert shared.status_code == 201
    assert ConversationProductInterest.objects.count() == 1


def test_plain_text_message_records_no_interest(customer, conversation):
    response = factories.client_for(customer).post(
        reverse(
            "chat:conversation-messages", kwargs={"conversation_id": conversation.pk}
        ),
        {"text": "hello"},
        format="json",
    )

    assert response.status_code == 201
    assert ConversationProductInterest.objects.count() == 0
