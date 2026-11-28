"""
Part BI-02 - product / content daily rollups.

Called from analytics.tasks.compute_daily_stats (no second task). Everything
is rebuilt from raw rows for ONE UTC day with update_or_create, so:
  - running a day twice gives the same rows (idempotent);
  - re-running a past day after new/removed raw rows refreshes it;
  - rows of that day that no longer have any activity are deleted (these
    tables are derived data, never a source of truth).

Orders are NOT rolled up: dashboards read the indexed orders table.

After a successful run the day is marked on BusinessDailyStats
(bi_rollups_computed_at). The dashboards use that mark to tell "0" from
"this day was never computed" (null).
"""

import logging

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from analytics.models import (
    BusinessDailyStats,
    ContentDailyStats,
    ContentViewEvent,
    ProductDailyStats,
    ProductViewEvent,
)
from analytics.utils import day_bounds
from chat.models import ConversationProductInterest
from content.models import Post, Reel
from products.models import Product
from social.models import Comment, Like, Save, Share

logger = logging.getLogger(__name__)


def _product_rows(target_date):
    start, end = day_bounds(target_date, target_date)
    rows = {}

    def slot(product_id):
        return rows.setdefault(
            product_id,
            {
                "business_id": None,
                "views": 0,
                "unique_views": 0,
                "interests": 0,
                "saves": 0,
            },
        )

    events = (
        ProductViewEvent.objects.filter(view_date=target_date)
        .values("product_id", "business_id")
        .order_by()
        .annotate(views=Count("id"), unique_views=Count("viewer_key", distinct=True))
    )
    for row in events:
        entry = slot(row["product_id"])
        entry["business_id"] = row["business_id"]
        entry["views"] = row["views"]
        entry["unique_views"] = row["unique_views"]

    interests = (
        ConversationProductInterest.objects.filter(
            first_opened_at__gte=start, first_opened_at__lt=end
        )
        .values("product_id", "business_id")
        .order_by()
        .annotate(n=Count("customer", distinct=True))
    )
    for row in interests:
        entry = slot(row["product_id"])
        entry["business_id"] = row["business_id"]
        entry["interests"] = row["n"]

    product_ct = ContentType.objects.get_for_model(Product)
    save_counts = {
        row["object_id"]: row["n"]
        for row in Save.objects.filter(
            content_type=product_ct, created_at__gte=start, created_at__lt=end
        )
        .values("object_id")
        .order_by()
        .annotate(n=Count("id"))
    }
    if save_counts:
        owners = dict(
            Product.all_objects.filter(pk__in=list(save_counts)).values_list(
                "pk", "business_id"
            )
        )
        for product_id, n in save_counts.items():
            if product_id not in owners:
                continue
            entry = slot(product_id)
            entry["saves"] = n
            if entry["business_id"] is None:
                entry["business_id"] = owners[product_id]

    return {pid: v for pid, v in rows.items() if v["business_id"] is not None}


def rollup_product_stats(target_date):
    rows = _product_rows(target_date)
    touched = []
    for product_id, values in rows.items():
        obj, _ = ProductDailyStats.objects.update_or_create(
            product_id=product_id,
            date=target_date,
            defaults={
                "business_id": values["business_id"],
                "views": values["views"],
                "unique_views": values["unique_views"],
                "interests": values["interests"],
                "saves": values["saves"],
            },
        )
        touched.append(obj.pk)
    ProductDailyStats.objects.filter(date=target_date).exclude(pk__in=touched).delete()
    return len(touched)


def _content_rows(target_date):
    start, end = day_bounds(target_date, target_date)
    post_ct = ContentType.objects.get_for_model(Post)
    reel_ct = ContentType.objects.get_for_model(Reel)
    kinds = {post_ct.pk: "post", reel_ct.pk: "reel"}
    rows = {}

    def slot(kind, object_id):
        return rows.setdefault(
            (kind, object_id),
            {
                "business_id": None,
                "views": 0,
                "unique_views": 0,
                "likes": 0,
                "comments": 0,
                "saves": 0,
                "shares": 0,
            },
        )

    events = (
        ContentViewEvent.objects.filter(view_date=target_date)
        .values("content_kind", "object_id", "business_id")
        .order_by()
        .annotate(views=Count("id"), unique_views=Count("viewer_key", distinct=True))
    )
    for row in events:
        entry = slot(row["content_kind"], row["object_id"])
        entry["business_id"] = row["business_id"]
        entry["views"] = row["views"]
        entry["unique_views"] = row["unique_views"]

    # Like/Save/Share: plain managers. Comment.objects hides soft-deleted
    # comments (same rule as P-084's total_comments_received).
    for field, model in (
        ("likes", Like),
        ("comments", Comment),
        ("saves", Save),
        ("shares", Share),
    ):
        grouped = (
            model.objects.filter(
                content_type_id__in=list(kinds),
                created_at__gte=start,
                created_at__lt=end,
            )
            .values("content_type_id", "object_id")
            .order_by()
            .annotate(n=Count("id"))
        )
        for row in grouped:
            slot(kinds[row["content_type_id"]], row["object_id"])[field] = row["n"]

    # Resolve the business for items that only had social activity. all_objects
    # so a Post/Reel deleted later still rolls up the same on recompute.
    for kind, model in (("post", Post), ("reel", Reel)):
        missing = [
            oid
            for (k, oid), v in rows.items()
            if k == kind and v["business_id"] is None
        ]
        if missing:
            owners = dict(
                model.all_objects.filter(pk__in=missing).values_list(
                    "pk", "business_id"
                )
            )
            for oid in missing:
                rows[(kind, oid)]["business_id"] = owners.get(oid)

    return {key: v for key, v in rows.items() if v["business_id"] is not None}


def rollup_content_stats(target_date):
    rows = _content_rows(target_date)
    touched = []
    for (kind, object_id), values in rows.items():
        obj, _ = ContentDailyStats.objects.update_or_create(
            content_kind=kind,
            object_id=object_id,
            date=target_date,
            defaults={
                "business_id": values["business_id"],
                "views": values["views"],
                "unique_views": values["unique_views"],
                "likes": values["likes"],
                "comments": values["comments"],
                "saves": values["saves"],
                "shares": values["shares"],
            },
        )
        touched.append(obj.pk)
    ContentDailyStats.objects.filter(date=target_date).exclude(pk__in=touched).delete()
    return len(touched)


def run_bi_rollups(target_date):
    """
    Product + content rollups for one UTC day, all-or-nothing, then mark the
    day on every BusinessDailyStats row of that date. If anything raises, the
    transaction rolls back and the previous mark (if any) is left untouched.
    """
    with transaction.atomic():
        product_rows = rollup_product_stats(target_date)
        content_rows = rollup_content_stats(target_date)
        marked = BusinessDailyStats.objects.filter(date=target_date).update(
            bi_rollups_computed_at=timezone.now()
        )
    return {
        "product_rows": product_rows,
        "content_rows": content_rows,
        "businesses_marked": marked,
    }
