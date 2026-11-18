"""
Shared factories for the BI-02 tests. Not a test module (no test_ prefix).

All dates are fixed past UTC days so results never depend on "today".
The factories in orders.tests.factories and core.tests.sweep_factories are
used with the same call shapes the existing tests use.
"""

import uuid
from datetime import date, datetime, time
from datetime import timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from analytics.models import (
    BusinessDailyStats,
    ContentDailyStats,
    ContentViewEvent,
    ProductDailyStats,
    ProductViewEvent,
)
from chat.models import ConversationProductInterest
from content.models import Post, Reel
from orders.models import Order
from orders.tests import factories as F

DAY1 = date(2026, 3, 10)
DAY2 = date(2026, 3, 11)
DAY3 = date(2026, 3, 12)
RANGE = {"date_from": "2026-03-10", "date_to": "2026-03-11"}


def at(day, hour=12, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=dt_timezone.utc)


def url(business, suffix):
    return f"/api/v1/analytics/business/{business.pk}/{suffix}"


def backdate(obj, when):
    type(obj)._base_manager.filter(pk=obj.pk).update(created_at=when)


def make_business():
    return F.make_business()


def make_customer():
    return F.make_user("customer")


def make_product(business, price="100.00", currency="EGP"):
    return F.make_product(business, price=price, currency=currency)


def make_post(business):
    return Post.objects.create(
        business=business, caption="bi02 post", status="published"
    )


def make_reel(business):
    reel = Reel.objects.create(
        business=business,
        caption="bi02 reel",
        video=SimpleUploadedFile(
            "raw.mp4",
            b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom",
            content_type="video/mp4",
        ),
    )
    Reel._base_manager.filter(pk=reel.pk).update(
        status="published", processing_status="ready"
    )
    reel.refresh_from_db()
    return reel


def mark_day(business, day, computed_at=None):
    row, _ = BusinessDailyStats.objects.update_or_create(
        business=business,
        date=day,
        defaults={"bi_rollups_computed_at": computed_at or timezone.now()},
    )
    return row


def make_order(
    business,
    product,
    customer,
    status="confirmed",
    quantity=1,
    unit_price="100.00",
    when=None,
    currency=None,
):
    conversation = F.make_conversation(business.user, customer)
    message = F.make_product_message(conversation, customer, product)
    unit = Decimal(unit_price)
    order = Order.objects.create(
        business=business,
        customer=customer,
        conversation=conversation,
        source_message=message,
        product=product,
        quantity=quantity,
        agreed_unit_price=unit,
        total_amount=unit * quantity,
        currency=currency or product.currency,
        status=status,
    )
    if when is not None:
        backdate(order, when)
    return order


def make_interest(business, product, customer, when):
    conversation = F.make_conversation(business.user, customer)
    return ConversationProductInterest.objects.create(
        business=business,
        product=product,
        conversation=conversation,
        customer=customer,
        source=ConversationProductInterest.Source.CONVERSATION_START,
        first_opened_at=when,
        last_opened_at=when,
    )


def make_product_event(product, viewer_key, when, event_id=None):
    return ProductViewEvent.objects.create(
        event_id=event_id or uuid.uuid4(),
        viewer_key=viewer_key,
        product=product,
        business=product.business,
        occurred_at=when,
        view_date=when.date(),
    )


def make_content_event(kind, obj, viewer_key, when, watch_ms=None):
    return ContentViewEvent.objects.create(
        event_id=uuid.uuid4(),
        viewer_key=viewer_key,
        content_kind=kind,
        object_id=obj.pk,
        business=obj.business,
        occurred_at=when,
        view_date=when.date(),
        watch_ms=watch_ms,
    )


def make_product_daily(product, day, views=0, unique_views=0, interests=0, saves=0):
    return ProductDailyStats.objects.create(
        business=product.business,
        product=product,
        date=day,
        views=views,
        unique_views=unique_views,
        interests=interests,
        saves=saves,
    )


def make_content_daily(kind, obj, day, **counts):
    return ContentDailyStats.objects.create(
        business=obj.business,
        content_kind=kind,
        object_id=obj.pk,
        date=day,
        **counts,
    )


def build_scenario():
    """
    One business, 2 EGP products, 3 customers, a post and a reel.
    Rollup days: DAY1 and DAY2 (both marked as computed).

    Expected numbers for RANGE (DAY1..DAY2):
      product views 19, unique views 13, saves 1
      interests: 3 (distinct customers 3)
      orders: 5 = confirmed 1, delivered 1, processing 1, pending 1, cancelled 1
      sold (confirmed+processing+shipped+delivered) = 3, units 4,
      sales EGP 450.00, aov 150.00
      funnel customers: interests 3, orders 3, confirmed 3, completed 1
    """
    business = make_business()
    p1 = make_product(business)
    p2 = make_product(business)
    c1, c2, c3 = make_customer(), make_customer(), make_customer()
    post = make_post(business)
    reel = make_reel(business)

    mark_day(business, DAY1)
    mark_day(business, DAY2)

    make_product_daily(p1, DAY1, views=10, unique_views=6, saves=1)
    make_product_daily(p1, DAY2, views=4, unique_views=3)
    make_product_daily(p2, DAY1, views=5, unique_views=4)

    make_interest(business, p1, c1, at(DAY1))
    make_interest(business, p1, c2, at(DAY1))
    make_interest(business, p2, c3, at(DAY2))

    make_order(business, p1, c1, "confirmed", 2, "100.00", at(DAY1))
    make_order(business, p1, c2, "delivered", 1, "150.00", at(DAY1))
    make_order(business, p2, c3, "pending", 1, "50.00", at(DAY2))
    make_order(business, p2, c1, "cancelled", 1, "75.00", at(DAY2))
    make_order(business, p1, c3, "processing", 1, "100.00", at(DAY2))

    return SimpleNamespace(
        business=business,
        p1=p1,
        p2=p2,
        c1=c1,
        c2=c2,
        c3=c3,
        post=post,
        reel=reel,
    )
