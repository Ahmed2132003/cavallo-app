"""
Part BI-01 — serializers for orders.

The CUSTOMER serializers list their fields explicitly and never include
`internal_note`, `is_internal` or the merchant's user id. Internal events
are filtered out in the queryset (is_internal=False) before they reach a
customer serializer, so hiding does not rely on the serializer alone.
"""

from decimal import Decimal

from rest_framework import serializers

from chat.serializers import _resolve_conversation_participant_display_name

from .constants import (
    ALLOWED_TRANSITIONS,
    MAX_NOTE_LENGTH,
    MAX_ORDER_QUANTITY,
    OrderStatus,
)
from .models import Order, OrderEvent

_MAX_ID = 2**63 - 1


class OrderFromChatSerializer(serializers.Serializer):
    """
    Input of POST /orders/from-chat/. Any other key (business_id,
    customer_id, product_id, total_amount, currency, ...) is not a declared
    field and is dropped: the server derives those itself.
    """

    conversation_id = serializers.IntegerField(min_value=1, max_value=_MAX_ID)
    source_message_id = serializers.IntegerField(min_value=1, max_value=_MAX_ID)
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_ORDER_QUANTITY)
    agreed_unit_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.01")
    )
    customer_note = serializers.CharField(
        required=False, allow_blank=True, max_length=MAX_NOTE_LENGTH, default=""
    )
    internal_note = serializers.CharField(
        required=False, allow_blank=True, max_length=MAX_NOTE_LENGTH, default=""
    )


class OrderStatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=OrderStatus.choices)
    note = serializers.CharField(
        required=False, allow_blank=True, max_length=MAX_NOTE_LENGTH, default=""
    )


class OrderNoteSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=MAX_NOTE_LENGTH)
    is_internal = serializers.BooleanField(required=False, default=False)


def _user_display_name(user):
    name = (getattr(user, "display_name", "") or "").strip()
    return name or _resolve_conversation_participant_display_name(user)


class MerchantOrderSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(read_only=True)
    customer = serializers.SerializerMethodField()
    product = serializers.SerializerMethodField()
    conversation = serializers.IntegerField(source="conversation_id", read_only=True)
    source_message = serializers.IntegerField(
        source="source_message_id", read_only=True
    )
    allowed_next_statuses = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "customer",
            "conversation",
            "source_message",
            "product",
            "quantity",
            "agreed_unit_price",
            "total_amount",
            "currency",
            "status",
            "allowed_next_statuses",
            "customer_note",
            "internal_note",
            "created_at",
            "updated_at",
            "confirmed_at",
            "completed_at",
            "cancelled_at",
        ]

    def get_customer(self, obj):
        return {"id": obj.customer_id, "display_name": _user_display_name(obj.customer)}

    def get_product(self, obj):
        return {"id": obj.product_id, "name": obj.product.name}

    def get_allowed_next_statuses(self, obj):
        return sorted(ALLOWED_TRANSITIONS.get(obj.status, frozenset()))


class MerchantOrderEventSerializer(serializers.ModelSerializer):
    actor = serializers.IntegerField(source="actor_id", read_only=True)

    class Meta:
        model = OrderEvent
        fields = [
            "id",
            "event_type",
            "from_status",
            "to_status",
            "note",
            "is_internal",
            "actor",
            "created_at",
        ]


class CustomerOrderEventSerializer(serializers.ModelSerializer):
    """No is_internal, no actor id: a customer only ever receives visible events."""

    class Meta:
        model = OrderEvent
        fields = ["id", "event_type", "from_status", "to_status", "note", "created_at"]


class CustomerOrderSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(read_only=True)
    business = serializers.SerializerMethodField()
    product = serializers.SerializerMethodField()
    conversation = serializers.IntegerField(source="conversation_id", read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "business",
            "conversation",
            "product",
            "quantity",
            "agreed_unit_price",
            "total_amount",
            "currency",
            "status",
            "customer_note",
            "created_at",
            "updated_at",
            "confirmed_at",
            "completed_at",
            "cancelled_at",
        ]

    def get_business(self, obj):
        return {"id": obj.business_id, "business_name": obj.business.business_name}

    def get_product(self, obj):
        return {"id": obj.product_id, "name": obj.product.name}


class CustomerOrderDetailSerializer(CustomerOrderSerializer):
    events = serializers.SerializerMethodField()

    class Meta(CustomerOrderSerializer.Meta):
        fields = CustomerOrderSerializer.Meta.fields + ["events"]

    def get_events(self, obj):
        visible = obj.events.filter(is_internal=False)
        return CustomerOrderEventSerializer(visible, many=True).data
