"""
Part BI-01 — business logic for Merchant-Recorded Orders.

All identities (business, customer, product, currency) are derived HERE
from the authenticated user and the source chat message. The request body
is never trusted for any of them.

Documented decisions
--------------------
* Inactive product (is_active=False): ALLOWED. The merchant only hid it
  from discovery; the deal was already agreed in chat, so the sale can
  still be recorded. A soft-deleted product is REJECTED (400).
* Idempotency: one order per source message. A repeated request returns
  the existing order (HTTP 200) and its body (quantity, price, notes) is
  ignored. Concurrent requests are serialised by a row lock on the source
  message; the UniqueConstraint on Order.source_message is the last line of
  defence (IntegrityError path -> return the existing order).
* Non-participant of the conversation -> 403 (same convention as chat).
  Order of other merchants -> 404 (never reveal that it exists).
"""

from decimal import ROUND_HALF_UP, Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from chat.models import Conversation, ConversationParticipant, Message
from products.models import Product
from django.contrib.auth import get_user_model

from .constants import ALLOWED_TRANSITIONS, OrderEventType, OrderStatus
from .models import Order, OrderEvent

_MONEY = Decimal("0.01")
_ERR_NOT_YOUR_PRODUCT = "The product of this message does not belong to your business."


def get_owner_business(user):
    """
    The user's own, non-deleted BusinessProfile, or 403. BusinessProfile's
    reverse accessor ignores the soft-delete manager, hence the explicit
    is_deleted check.
    """
    business = getattr(user, "business_profile", None)
    if business is None or business.is_deleted:
        raise PermissionDenied("A business profile is required to manage orders.")
    return business


def compute_total(quantity, unit_price):
    return (Decimal(quantity) * Decimal(unit_price)).quantize(
        _MONEY, rounding=ROUND_HALF_UP
    )


def _get_existing_order(message):
    return Order.objects.filter(source_message=message).first()


def _resolve_message_product(message):
    """The Product shared by `message`, or a 400 saying why there is none."""
    product_ct = ContentType.objects.get_for_model(Product)
    if message.shared_content_type_id != product_ct.id or (
        message.shared_object_id is None
    ):
        raise ValidationError(
            {"source_message_id": ["This message is not linked to a product."]}
        )
    product = Product.objects.filter(pk=message.shared_object_id).first()
    if product is None:
        raise ValidationError(
            {
                "source_message_id": [
                    "The product of this message is no longer available."
                ]
            }
        )
    return product


def create_order_from_chat(
    *,
    user,
    conversation_id,
    source_message_id,
    quantity,
    agreed_unit_price,
    customer_note="",
    internal_note="",
):
    """
    Record an order from a chat message. Returns (order, created).
    created=False means the message already had an order (idempotent).
    """
    business = get_owner_business(user)

    conversation = Conversation.objects.filter(pk=conversation_id).first()
    if conversation is None:
        raise NotFound("Conversation not found.")
    participant_ids = list(
        ConversationParticipant.objects.filter(conversation=conversation).values_list(
            "user_id", flat=True
        )
    )
    if user.id not in participant_ids:
        raise PermissionDenied("You are not a participant of this conversation.")
    others = [uid for uid in participant_ids if uid != user.id]
    if len(others) != 1:
        raise ValidationError(
            {"conversation_id": ["This conversation has no customer."]}
        )
    customer_id = others[0]

    customer_user = (
        get_user_model().objects.filter(pk=customer_id).only("account_type").first()
    )
    if customer_user is None or customer_user.account_type != "customer":
        raise ValidationError(
            {"conversation_id": ["The other participant is not a customer."]}
        )

    # A message from another conversation and a missing message look the
    # same on purpose.
    message = Message.objects.filter(
        pk=source_message_id, conversation=conversation
    ).first()
    if message is None:
        raise ValidationError(
            {"source_message_id": ["Message not found in this conversation."]}
        )

    # Idempotent replay is checked BEFORE product validation so repeating a
    # request keeps working even if the product was deleted afterwards.
    existing = _get_existing_order(message)
    if existing is not None:
        if existing.business_id != business.id:
            raise ValidationError({"source_message_id": [_ERR_NOT_YOUR_PRODUCT]})
        return existing, False

    product = _resolve_message_product(message)
    if product.business_id != business.id:
        raise ValidationError({"source_message_id": [_ERR_NOT_YOUR_PRODUCT]})

    with transaction.atomic():
        # Serialise concurrent requests for the same message.
        locked = Message.objects.select_for_update().filter(pk=message.pk).first()
        if locked is None:
            raise ValidationError(
                {"source_message_id": ["Message not found in this conversation."]}
            )
        existing = _get_existing_order(message)
        if existing is not None:
            return existing, False
        try:
            with transaction.atomic():
                order = Order.objects.create(
                    business=business,
                    customer_id=customer_id,
                    conversation=conversation,
                    source_message=message,
                    product=product,
                    quantity=quantity,
                    agreed_unit_price=agreed_unit_price,
                    total_amount=compute_total(quantity, agreed_unit_price),
                    currency=product.currency,
                    customer_note=customer_note,
                    internal_note=internal_note,
                )
                OrderEvent.objects.create(
                    order=order,
                    actor=user,
                    event_type=OrderEventType.CREATED,
                    to_status=OrderStatus.PENDING.value,
                )
        except IntegrityError:
            existing = _get_existing_order(message)
            if existing is None:
                raise
            return existing, False
    return order, True


def change_order_status(*, order_id, business, actor, new_status, note=""):
    """
    Move an order of `business` to `new_status` if ALLOWED_TRANSITIONS says
    so. The row is locked, the check and the write happen in one
    transaction, and an OrderEvent is appended. The `note` is visible to the
    customer (use add_order_note(is_internal=True) for private remarks).
    """
    with transaction.atomic():
        order = (
            Order.objects.select_for_update()
            .filter(pk=order_id, business=business)
            .first()
        )
        if order is None:
            raise NotFound("Order not found.")

        current = order.status
        if new_status == current:
            raise ValidationError({"status": [f"The order is already '{current}'."]})
        if new_status not in ALLOWED_TRANSITIONS.get(current, frozenset()):
            raise ValidationError(
                {
                    "status": [
                        f"Cannot change an order from '{current}' to '{new_status}'."
                    ]
                }
            )

        now = timezone.now()
        update_fields = ["status", "updated_at"]
        order.status = new_status
        if new_status == OrderStatus.CONFIRMED and order.confirmed_at is None:
            order.confirmed_at = now
            update_fields.append("confirmed_at")
        elif new_status == OrderStatus.DELIVERED:
            order.completed_at = now
            update_fields.append("completed_at")
        elif new_status == OrderStatus.CANCELLED:
            order.cancelled_at = now
            update_fields.append("cancelled_at")
        order.save(update_fields=update_fields)

        OrderEvent.objects.create(
            order=order,
            actor=actor,
            event_type=OrderEventType.STATUS_CHANGED,
            from_status=current,
            to_status=new_status,
            note=note,
            is_internal=False,
        )
    return order


def add_order_note(*, order_id, business, actor, note, is_internal):
    """Append a note event to an order of `business`. Returns the event."""
    order = Order.objects.filter(pk=order_id, business=business).first()
    if order is None:
        raise NotFound("Order not found.")
    return OrderEvent.objects.create(
        order=order,
        actor=actor,
        event_type=OrderEventType.NOTE_ADDED,
        note=note,
        is_internal=is_internal,
    )
