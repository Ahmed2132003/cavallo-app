"""
PART BI-01 - model-level guarantees the Analytics queries depend on.
"""

import pytest

from orders.models import Order, OrderEvent
from orders.tests import factories

pytestmark = pytest.mark.django_db


def _index_field_sets():
    return {
        tuple(name.lstrip("-") for name in index.fields)
        for index in Order._meta.indexes
    }


def test_analytics_indexes_exist():
    index_fields = _index_field_sets()

    assert ("business", "status", "created_at") in index_fields
    assert ("product", "status") in index_fields


def test_source_message_has_a_unique_constraint():
    field = Order._meta.get_field("source_message")
    unique_together = [set(group) for group in Order._meta.unique_together]
    constraint_fields = [
        set(constraint.fields)
        for constraint in Order._meta.constraints
        if hasattr(constraint, "fields")
    ]

    assert (
        field.unique
        or {"source_message"} in unique_together
        or {"source_message"} in constraint_fields
    )


def test_order_events_keep_their_order_after_new_ones_are_added(
    merchant, customer, product
):
    order = factories.make_order(merchant, customer, product)

    first = factories.make_event(order, merchant.user, note="first")
    second = factories.make_event(order, merchant.user, note="second")

    notes = list(
        OrderEvent.objects.filter(order=order)
        .order_by("created_at", "pk")
        .values_list("note", flat=True)
    )
    assert notes == ["first", "second"]
    assert first.pk < second.pk
