"""
Shared helpers for the BI-01 tests (orders app + chat product interest).

No factory_boy (no new dependencies): plain functions that build real rows
through the ORM. Every model import is lazy so this module can be loaded as
a pytest plugin before Django's app registry is ready.
"""

import itertools
from decimal import Decimal

_counter = itertools.count(1)


def _next():
    return next(_counter)


def dec(value):
    """DRF may render decimals as strings or numbers; compare as Decimal."""
    return Decimal(str(value))


def client_for(user):
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=user)
    return client


def results(response):
    """Rows of a list response, paginated (results/items) or plain list."""
    data = response.json()
    if isinstance(data, dict):
        for key in ("results", "items"):
            if key in data:
                return data[key]
    return data


def order_id_of(value):
    """The message `order` field may be an id or a nested object."""
    if isinstance(value, dict):
        return value["id"]
    return value


def make_user(account_type="customer"):
    from django.contrib.auth import get_user_model

    n = _next()
    return get_user_model().objects.create_user(
        username=f"bi01_user_{n}",
        email=f"bi01_user_{n}@example.com",
        password="Pass12345!x",
        account_type=account_type,
    )


def make_business(user=None, business_type="trader"):
    from businesses.models import BusinessProfile

    if user is None:
        user = make_user("business")
    return BusinessProfile.objects.create(
        user=user,
        business_name=f"BI01 Business {_next()}",
        business_type=business_type,
        country="Egypt",
        city="Cairo",
    )


def get_category():
    from categories.models import Category

    category = Category.objects.first()
    if category is not None:
        return category
    field_names = {field.name for field in Category._meta.get_fields()}
    kwargs = {}
    if "name" in field_names:
        kwargs["name"] = "BI01 Category"
    if "slug" in field_names:
        kwargs["slug"] = "bi01-category"
    return Category.objects.create(**kwargs)


def make_product(business, price="125.50", currency="EGP", is_active=True):
    from products.models import Product

    return Product.objects.create(
        business=business,
        category=get_category(),
        name=f"BI01 Product {_next()}",
        price=Decimal(price),
        currency=currency,
        is_active=is_active,
    )


def make_conversation(user_a, user_b):
    from chat.models import Conversation, ConversationParticipant

    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user_a)
    ConversationParticipant.objects.create(conversation=conversation, user=user_b)
    return conversation


def make_message(conversation, sender, text="hello"):
    from chat.models import Message

    return Message.objects.create(conversation=conversation, sender=sender, text=text)


def make_product_message(conversation, sender, product):
    from django.contrib.contenttypes.models import ContentType

    from chat.models import Message
    from products.models import Product

    return Message.objects.create(
        conversation=conversation,
        sender=sender,
        text="",
        shared_content_type=ContentType.objects.get_for_model(Product),
        shared_object_id=product.pk,
    )


def make_order(
    merchant,
    customer,
    product,
    conversation=None,
    message=None,
    quantity=1,
    unit_price="100.00",
    status="pending",
    customer_note="",
    internal_note="",
):
    """Insert an Order straight through the ORM (no OrderEvent rows)."""
    from orders.models import Order

    if conversation is None:
        conversation = make_conversation(merchant.user, customer)
    if message is None:
        message = make_product_message(conversation, customer, product)
    price = Decimal(unit_price)
    return Order.objects.create(
        business=merchant,
        customer=customer,
        conversation=conversation,
        source_message=message,
        product=product,
        quantity=quantity,
        agreed_unit_price=price,
        total_amount=price * quantity,
        currency=product.currency,
        status=status,
        customer_note=customer_note,
        internal_note=internal_note,
    )


def make_event(
    order,
    actor,
    event_type="note",
    from_status="",
    to_status="",
    note="",
    is_internal=False,
):
    from orders.models import OrderEvent

    return OrderEvent.objects.create(
        order=order,
        actor=actor,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        note=note,
        is_internal=is_internal,
    )


def from_chat_payload(conversation, message, **overrides):
    payload = {
        "conversation_id": conversation.pk,
        "source_message_id": message.pk,
        "quantity": 2,
        "agreed_unit_price": "100.00",
        "customer_note": "customer note",
        "internal_note": "internal note",
    }
    payload.update(overrides)
    return payload
