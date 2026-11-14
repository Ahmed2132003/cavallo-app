"""
Part BI-01 — fixed definitions for Merchant-Recorded Orders.

ARCHITECTURE DECISION
---------------------
An Order here is a sale the MERCHANT records by hand after agreeing it in
chat. The platform has NO cart, NO checkout, NO payment and NO inventory:
nothing in this app moves money or decrements stock, and Product never
grows a stock/SKU field because of it.

SALES VALUE (single source of truth for Analytics)
--------------------------------------------------
Sales Value = SUM(total_amount) over orders whose status is in
SALES_VALUE_STATUSES (confirmed / processing / shipped / delivered).
`pending` (not yet accepted by the merchant) and `cancelled` are excluded.

This is merchant-RECORDED sales, NOT payments received: the platform never
sees a payment, so no figure derived from it may be labelled "revenue
collected" or "paid".

Totals are only meaningful per currency; never sum across currencies.
"""

from django.db import models


class OrderStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    PROCESSING = "processing", "Processing"
    SHIPPED = "shipped", "Shipped"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"


# The ONLY place that says which status changes are legal.
#   pending -> confirmed -> processing -> shipped -> delivered
# and an order may be cancelled from any state before it is delivered
# (shipped -> cancelled covers a failed delivery / return).
# `delivered` and `cancelled` are terminal: no way out, so e.g.
# cancelled -> delivered and delivered -> cancelled are rejected.
# Skipping steps (pending -> shipped) is rejected too.
ALLOWED_TRANSITIONS = {
    OrderStatus.PENDING.value: frozenset(
        {OrderStatus.CONFIRMED.value, OrderStatus.CANCELLED.value}
    ),
    OrderStatus.CONFIRMED.value: frozenset(
        {OrderStatus.PROCESSING.value, OrderStatus.CANCELLED.value}
    ),
    OrderStatus.PROCESSING.value: frozenset(
        {OrderStatus.SHIPPED.value, OrderStatus.CANCELLED.value}
    ),
    OrderStatus.SHIPPED.value: frozenset(
        {OrderStatus.DELIVERED.value, OrderStatus.CANCELLED.value}
    ),
    OrderStatus.DELIVERED.value: frozenset(),
    OrderStatus.CANCELLED.value: frozenset(),
}

# Statuses that count towards Sales Value (see module docstring).
SALES_VALUE_STATUSES = (
    OrderStatus.CONFIRMED.value,
    OrderStatus.PROCESSING.value,
    OrderStatus.SHIPPED.value,
    OrderStatus.DELIVERED.value,
)
EXCLUDED_FROM_SALES_VALUE_STATUSES = (
    OrderStatus.PENDING.value,
    OrderStatus.CANCELLED.value,
)


class OrderEventType(models.TextChoices):
    CREATED = "created", "Created"
    STATUS_CHANGED = "status_changed", "Status changed"
    NOTE_ADDED = "note_added", "Note added"


# Input limits (serializer layer). 100_000 units * 99_999_999.99 still fits
# Order.total_amount (max_digits=16).
MAX_ORDER_QUANTITY = 100_000
MAX_NOTE_LENGTH = 2000

ORDER_NUMBER_PREFIX = "ORD-"


def format_order_number(order_id):
    """Public order number shown to both sides, e.g. ORD-42."""
    return f"{ORDER_NUMBER_PREFIX}{order_id}"


def parse_order_number(raw):
    """
    Accepts "42", "#42", "ORD-42" (any case) and returns the id, or None
    when the text is not an order number. Used by the merchant list search.
    """
    value = (raw or "").strip().upper()
    if value.startswith(ORDER_NUMBER_PREFIX):
        value = value[len(ORDER_NUMBER_PREFIX) :]
    value = value.lstrip("#").strip()
    if value.isascii() and value.isdigit() and len(value) <= 18:
        return int(value)
    return None
