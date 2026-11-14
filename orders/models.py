"""
Part BI-01 — Order + OrderEvent (Merchant-Recorded Orders).

See orders/constants.py for the architecture decision and the Sales Value
definition. Every identity on an Order (business, customer, product,
currency) is resolved by the SERVER from the source chat message in
orders/services.py; nothing here is ever taken from client input.

on_delete=PROTECT everywhere an Order points at another row: an order is a
business record, so a hard delete of its business / customer / product /
conversation / message must fail loudly rather than silently erase it
(same reasoning as Product.business). Product and BusinessProfile are
soft-deleted in normal operation, so this never blocks day-to-day use.
"""

from django.conf import settings
from django.db import models

from core.models import TimestampedModel

from .constants import OrderEventType, OrderStatus, format_order_number


class Order(TimestampedModel):
    business = models.ForeignKey(
        "businesses.BusinessProfile",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders_as_customer",
    )
    conversation = models.ForeignKey(
        "chat.Conversation",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    # The chat message (a shared Product) the merchant recorded the sale
    # from. UniqueConstraint below = at most ONE order per message.
    # Reverse accessor `message.orders` is what chat's MessageSerializer
    # reads to fill its `order` field (no import from orders needed).
    source_message = models.ForeignKey(
        "chat.Message",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    quantity = models.PositiveIntegerField()
    # SNAPSHOTS taken at recording time: later changes to Product.price /
    # Product.currency never touch an existing order.
    agreed_unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_amount = models.DecimalField(max_digits=16, decimal_places=2)
    currency = models.CharField(max_length=3)
    status = models.CharField(
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING,
    )
    # customer_note is shown to the customer; internal_note NEVER is (see
    # orders/serializers.py: the customer serializers do not list it).
    customer_note = models.TextField(blank=True, default="")
    internal_note = models.TextField(blank=True, default="")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_message"],
                name="orders_unique_source_message",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="orders_quantity_gte_1",
            ),
        ]
        indexes = [
            # Analytics: sales per business / status over time.
            models.Index(
                fields=["business", "status", "created_at"],
                name="orders_biz_status_created_idx",
            ),
            # Analytics: orders per product / status.
            models.Index(
                fields=["product", "status"],
                name="orders_product_status_idx",
            ),
            # Customer "my orders" list.
            models.Index(
                fields=["customer", "created_at"],
                name="orders_customer_created_idx",
            ),
        ]

    def __str__(self):
        return self.order_number

    @property
    def order_number(self):
        return format_order_number(self.pk)


class OrderEvent(models.Model):
    """
    Append-only audit trail of an order. Rows are only ever INSERTED:
    save() on an existing row and delete() raise. (QuerySet.update() /
    QuerySet.delete() bypass model methods in Django; nothing in the code
    base calls them on this model, and the admin is read-only.)

    is_internal=True events are merchant-only and must never reach a
    customer serializer.
    """

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_events",
    )
    event_type = models.CharField(max_length=20, choices=OrderEventType.choices)
    from_status = models.CharField(max_length=20, blank=True, default="")
    to_status = models.CharField(max_length=20, blank=True, default="")
    note = models.TextField(blank=True, default="")
    is_internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(
                fields=["order", "created_at"],
                name="orders_event_order_created_idx",
            ),
        ]

    def __str__(self):
        return f"{self.event_type} on order #{self.order_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise RuntimeError("OrderEvent is append-only and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("OrderEvent is append-only and cannot be deleted.")
