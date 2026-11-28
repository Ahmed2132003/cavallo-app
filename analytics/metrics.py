"""
Part BI-02 - read side of the dashboard APIs.

Nothing here scans raw view events: views/engagement come from the daily
rollup tables, orders and interests come straight from their own indexed
tables. Every function takes an already-authorised BusinessProfile.

Missing data is None, never 0: a view-derived number is None when no day of
the requested range has been rolled up (see rollup_coverage). Order and
interest numbers are real counts, so 0 means 0.
"""

from collections import defaultdict
from datetime import timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import serializers

from analytics import constants as C
from analytics.external import latest_snapshots
from analytics.models import BusinessDailyStats, ContentDailyStats, ProductDailyStats
from analytics.utils import day_bounds, iso, money, ratio
from chat.models import ConversationProductInterest
from content.models import Post, Reel
from orders.models import Order
from products.models import Product

PRODUCT_ORDERING = {
    "views": "views",
    "unique_views": "unique_views",
    "interests": "interests",
    "orders": "orders",
    "sales": "_sales_sort",
    "conversion": "conversion",
}
CONTENT_ORDERING = {
    "created_at": "created_at",
    "views": "views",
    "unique_views": "unique_views",
    "likes": "likes",
    "comments": "comments",
    "saves": "saves",
    "in_app_shares": "in_app_shares",
    "engagement_rate": "engagement_rate",
}
SCALAR_KEYS = (
    "product_views",
    "product_unique_views",
    "interests",
    "interested_customers",
    "orders",
    "pending_orders",
    "confirmed_orders",
    "completed_orders",
    "cancelled_orders",
    "units",
    "content_views",
    "content_unique_views",
    "likes",
    "comments",
    "saves",
    "in_app_shares",
)
FUNNEL_STAGES = ("views", "interests", "orders", "confirmed", "completed")


# --------------------------------------------------------------- range / paging
def resolve_range(params, default_to):
    """(date_from, date_to, label). date_from wins over period."""
    date_to = params.get("date_to") or default_to
    if params.get("date_from"):
        date_from = params["date_from"]
        label = "custom"
    else:
        label = params.get("period") or C.DEFAULT_PERIOD
        date_from = date_to - timedelta(days=C.PERIOD_DAYS[label] - 1)
    if date_from > date_to:
        raise serializers.ValidationError(
            {"date_from": ["date_from must be on or before date_to."]}
        )
    if (date_to - date_from).days + 1 > C.MAX_RANGE_DAYS:
        raise serializers.ValidationError(
            {"date_from": [f"The range cannot exceed {C.MAX_RANGE_DAYS} days."]}
        )
    return date_from, date_to, label


def period_dict(date_from, date_to, label=None):
    data = {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "days": (date_to - date_from).days + 1,
    }
    if label is not None:
        data["period"] = label
    return data


def parse_ordering(raw, allowed, default):
    value = (raw or default).strip()
    descending = value.startswith("-")
    field = value.lstrip("-+")
    if field not in allowed:
        raise serializers.ValidationError(
            {
                "ordering": [
                    "Unsupported ordering. Allowed: "
                    + ", ".join(sorted(allowed))
                    + " (prefix '-' for descending)."
                ]
            }
        )
    return allowed[field], descending


def sort_rows(rows, key, descending):
    """Rows whose value is None always go last, in both directions."""
    present = [r for r in rows if r.get(key) is not None]
    absent = [r for r in rows if r.get(key) is None]
    present.sort(key=lambda r: r[key], reverse=descending)
    return present + absent


def paginate(rows, page, page_size):
    count = len(rows)
    total_pages = max(1, -(-count // page_size))
    start = (page - 1) * page_size
    results = [
        {k: v for k, v in row.items() if not k.startswith("_")}
        for row in rows[start : start + page_size]
    ]
    return {
        "count": count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "results": results,
    }


# -------------------------------------------------------------------- coverage
def default_date_to(business):
    """Last day whose BI rollups exist, else yesterday (UTC)."""
    last = BusinessDailyStats.objects.filter(
        business=business, bi_rollups_computed_at__isnull=False
    ).aggregate(m=Max("date"))["m"]
    return last or (timezone.localdate() - timedelta(days=1))


def rollup_coverage(business, date_from, date_to):
    days = (date_to - date_from).days + 1
    agg = BusinessDailyStats.objects.filter(
        business=business,
        date__range=(date_from, date_to),
        bi_rollups_computed_at__isnull=False,
    ).aggregate(n=Count("id"), updated=Max("bi_rollups_computed_at"))
    n = agg["n"] or 0
    return {
        "days_in_range": days,
        "rollup_days": n,
        "views_available": n > 0,
        "views_complete": n == days,
        "updated": agg["updated"],
    }


def public_coverage(cov):
    return {k: v for k, v in cov.items() if k != "updated"}


def covered_days(business, date_from, date_to):
    return set(
        BusinessDailyStats.objects.filter(
            business=business,
            date__range=(date_from, date_to),
            bi_rollups_computed_at__isnull=False,
        ).values_list("date", flat=True)
    )


# ------------------------------------------------------------------- aggregates
def product_rollup_totals(business, date_from, date_to):
    agg = ProductDailyStats.objects.filter(
        business=business, date__range=(date_from, date_to)
    ).aggregate(
        views=Sum("views"), unique_views=Sum("unique_views"), saves=Sum("saves")
    )
    return {k: (v or 0) for k, v in agg.items()}


def content_rollup_totals(business, date_from, date_to, kinds=C.CONTENT_KINDS):
    agg = ContentDailyStats.objects.filter(
        business=business, content_kind__in=kinds, date__range=(date_from, date_to)
    ).aggregate(
        views=Sum("views"),
        unique_views=Sum("unique_views"),
        likes=Sum("likes"),
        comments=Sum("comments"),
        saves=Sum("saves"),
        shares=Sum("shares"),
    )
    return {k: (v or 0) for k, v in agg.items()}


def interest_counts(business, date_from, date_to, product=None):
    start, end = day_bounds(date_from, date_to)
    queryset = ConversationProductInterest.objects.filter(
        business=business, first_opened_at__gte=start, first_opened_at__lt=end
    )
    if product is not None:
        queryset = queryset.filter(product=product)
    return queryset.aggregate(
        interests=Count("id"), customers=Count("customer", distinct=True)
    )


def _order_queryset(business, date_from, date_to, product=None):
    start, end = day_bounds(date_from, date_to)
    queryset = Order.objects.filter(
        business=business, created_at__gte=start, created_at__lt=end
    )
    if product is not None:
        queryset = queryset.filter(product=product)
    return queryset


def order_stage_counts(business, date_from, date_to, product=None):
    """
    One query. Period = order created_at (UTC). Confirmed = status in
    SALES_STATUSES (confirmed and beyond, not cancelled); Completed =
    delivered. ``customers*`` are distinct customers.
    """
    sold = Q(status__in=C.SALES_STATUSES)
    done = Q(status=C.STATUS_COMPLETED)
    agg = _order_queryset(business, date_from, date_to, product).aggregate(
        orders=Count("id"),
        customers=Count("customer", distinct=True),
        pending=Count("id", filter=Q(status=C.STATUS_PENDING)),
        confirmed=Count("id", filter=sold),
        completed=Count("id", filter=done),
        cancelled=Count("id", filter=Q(status=C.STATUS_CANCELLED)),
        customers_confirmed=Count("customer", filter=sold, distinct=True),
        customers_completed=Count("customer", filter=done, distinct=True),
        units=Sum("quantity", filter=sold),
        updated=Max("updated_at"),
    )
    agg["units"] = agg["units"] or 0
    return agg


def sales_by_currency(business, date_from, date_to, product=None):
    """Sales Value per currency (never summed across currencies)."""
    rows = (
        _order_queryset(business, date_from, date_to, product)
        .filter(status__in=C.SALES_STATUSES)
        .values("currency")
        .order_by("currency")
        .annotate(
            sales_value=Sum("total_amount"),
            sold_orders=Count("id"),
            units=Sum("quantity"),
        )
    )
    return [
        {
            "currency": r["currency"],
            "sales_value": money(r["sales_value"]),
            "sold_orders": r["sold_orders"],
            "units": r["units"] or 0,
            "aov": money(r["sales_value"] / r["sold_orders"]),
        }
        for r in rows
    ]


def engagement_rate(likes, comments, saves, shares, unique_views):
    if None in (likes, comments, saves, shares, unique_views):
        return None
    return ratio(likes + comments + saves + shares, unique_views)


# ------------------------------------------------------------------------ funnel
def build_funnel(*, views, interested, ordering, confirmed, completed, views_complete):
    counts = {
        "views": views,
        "interests": interested,
        "orders": ordering,
        "confirmed": confirmed,
        "completed": completed,
    }
    conversions = []
    for a, b in zip(FUNNEL_STAGES, FUNNEL_STAGES[1:]):
        rate = ratio(counts[b], counts[a])
        if a == "views" and not views_complete:
            rate = None
        conversions.append({"from": a, "to": b, "rate": rate})
    overall = ratio(counts["completed"], counts["views"]) if views_complete else None
    return {
        "unit_note": (
            "views = unique views (viewer per item per UTC day); the other stages "
            "are distinct customers. Rates are stage / previous stage (fraction), "
            "null when the previous stage is 0 or view coverage is incomplete."
        ),
        "stages": [{"key": key, "count": counts[key]} for key in FUNNEL_STAGES],
        "conversions": conversions,
        "overall_rate": overall,
    }


# ---------------------------------------------------------------------- overview
def build_overview(business, date_from, date_to):
    cov = rollup_coverage(business, date_from, date_to)
    available = cov["views_available"]
    prod = product_rollup_totals(business, date_from, date_to) if available else None
    cont = content_rollup_totals(business, date_from, date_to) if available else None
    interests = interest_counts(business, date_from, date_to)
    orders = order_stage_counts(business, date_from, date_to)
    overview = {
        "product_views": prod["views"] if prod else None,
        "product_unique_views": prod["unique_views"] if prod else None,
        "interests": interests["interests"],
        "interested_customers": interests["customers"],
        "orders": orders["orders"],
        "pending_orders": orders["pending"],
        "confirmed_orders": orders["confirmed"],
        "completed_orders": orders["completed"],
        "cancelled_orders": orders["cancelled"],
        "units": orders["units"],
        "sales": sales_by_currency(business, date_from, date_to),
        "content_views": cont["views"] if cont else None,
        "content_unique_views": cont["unique_views"] if cont else None,
        "likes": cont["likes"] if cont else None,
        "comments": cont["comments"] if cont else None,
        "saves": cont["saves"] if cont else None,
        "in_app_shares": cont["shares"] if cont else None,
    }
    funnel = build_funnel(
        views=prod["unique_views"] if prod else None,
        interested=interests["customers"],
        ordering=orders["customers"],
        confirmed=orders["customers_confirmed"],
        completed=orders["customers_completed"],
        views_complete=cov["views_complete"],
    )
    return {
        "overview": overview,
        "funnel": funnel,
        "coverage": public_coverage(cov),
        "data_updated_at": iso(cov["updated"]),
        "orders_data_updated_at": iso(orders["updated"]),
    }


def delta(current, previous):
    if current is None or previous is None:
        return {
            "current": current,
            "previous": previous,
            "change": None,
            "change_pct": None,
        }
    change = current - previous
    return {
        "current": current,
        "previous": previous,
        "change": change,
        "change_pct": ratio(change, previous),
    }


def build_previous_period(business, date_from, date_to, current):
    """
    Same-length window right before the current one, or None unless BOTH
    windows are fully covered by rollups (no percentages on missing data).
    """
    if not current["coverage"]["views_complete"]:
        return None
    length = (date_to - date_from).days + 1
    prev_to = date_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=length - 1)
    prev = build_overview(business, prev_from, prev_to)
    if not prev["coverage"]["views_complete"]:
        return None
    return {
        "period": period_dict(prev_from, prev_to),
        "overview": prev["overview"],
        "funnel": prev["funnel"],
        "coverage": prev["coverage"],
        "data_updated_at": prev["data_updated_at"],
        "deltas": {
            key: delta(current["overview"][key], prev["overview"][key])
            for key in SCALAR_KEYS
        },
    }


# ---------------------------------------------------------------------- products
def build_product_rows(business, date_from, date_to, cov):
    products = list(
        Product.objects.filter(business=business).values(
            "id", "name", "currency", "is_active"
        )
    )
    available = cov["views_available"]
    start, end = day_bounds(date_from, date_to)

    roll = {}
    if available:
        grouped = (
            ProductDailyStats.objects.filter(
                business=business, date__range=(date_from, date_to)
            )
            .values("product_id")
            .order_by()
            .annotate(
                views=Sum("views"),
                unique_views=Sum("unique_views"),
                saves=Sum("saves"),
            )
        )
        roll = {r["product_id"]: r for r in grouped}

    interests = {
        r["product_id"]: r["n"]
        for r in ConversationProductInterest.objects.filter(
            business=business, first_opened_at__gte=start, first_opened_at__lt=end
        )
        .values("product_id")
        .order_by()
        .annotate(n=Count("customer", distinct=True))
    }

    sold = Q(status__in=C.SALES_STATUSES)
    order_qs = Order.objects.filter(
        business=business, created_at__gte=start, created_at__lt=end
    )
    orders = {
        r["product_id"]: r
        for r in order_qs.values("product_id")
        .order_by()
        .annotate(
            orders=Count("id"),
            sold_orders=Count("id", filter=sold),
            units=Sum("quantity", filter=sold),
        )
    }
    sales = defaultdict(list)
    for r in (
        order_qs.filter(sold)
        .values("product_id", "currency")
        .order_by("product_id", "currency")
        .annotate(v=Sum("total_amount"))
    ):
        sales[r["product_id"]].append(r)

    rows = []
    for p in products:
        pid = p["id"]
        rr = roll.get(pid)
        views = (rr["views"] if rr else 0) if available else None
        unique_views = (rr["unique_views"] if rr else 0) if available else None
        saves = (rr["saves"] if rr else 0) if available else None
        o = orders.get(pid) or {}
        sold_orders = o.get("sold_orders", 0)
        rows.append(
            {
                "product_id": pid,
                "name": p["name"],
                "is_active": p["is_active"],
                "currency": p["currency"],
                "views": views,
                "unique_views": unique_views,
                "interests": interests.get(pid, 0),
                "saves": saves,
                "orders": o.get("orders", 0),
                "sold_orders": sold_orders,
                "units": o.get("units") or 0,
                "sales": [
                    {"currency": s["currency"], "sales_value": money(s["v"])}
                    for s in sales.get(pid, [])
                ],
                "conversion": (
                    ratio(sold_orders, unique_views) if cov["views_complete"] else None
                ),
                "_sales_sort": sum((s["v"] for s in sales.get(pid, [])), Decimal("0")),
            }
        )
    return rows


def _orders_by_day(queryset):
    """{date: {"orders", "sold", "sales": {currency: Decimal}}}"""
    grouped = (
        queryset.annotate(day=TruncDate("created_at", tzinfo=dt_timezone.utc))
        .values("day", "status", "currency")
        .order_by()
        .annotate(n=Count("id"), v=Sum("total_amount"))
    )
    per_day = {}
    for r in grouped:
        entry = per_day.setdefault(r["day"], {"orders": 0, "sold": 0, "sales": {}})
        entry["orders"] += r["n"]
        if r["status"] in C.SALES_STATUSES:
            entry["sold"] += r["n"]
            entry["sales"][r["currency"]] = entry["sales"].get(
                r["currency"], Decimal("0")
            ) + (r["v"] or Decimal("0"))
    return per_day


def _days(date_from, date_to):
    day = date_from
    while day <= date_to:
        yield day
        day += timedelta(days=1)


def build_product_detail(business, product, date_from, date_to):
    cov = rollup_coverage(business, date_from, date_to)
    available = cov["views_available"]
    covered = covered_days(business, date_from, date_to)

    agg = ProductDailyStats.objects.filter(
        product=product, date__range=(date_from, date_to)
    ).aggregate(
        views=Sum("views"), unique_views=Sum("unique_views"), saves=Sum("saves")
    )
    views = (agg["views"] or 0) if available else None
    unique_views = (agg["unique_views"] or 0) if available else None
    saves = (agg["saves"] or 0) if available else None

    interests = interest_counts(business, date_from, date_to, product)
    orders = order_stage_counts(business, date_from, date_to, product)

    rolls = {
        r.date: r
        for r in ProductDailyStats.objects.filter(
            product=product, date__range=(date_from, date_to)
        )
    }
    per_day = _orders_by_day(_order_queryset(business, date_from, date_to, product))
    trend = []
    for day in _days(date_from, date_to):
        is_covered = day in covered
        rr = rolls.get(day)
        o = per_day.get(day)
        trend.append(
            {
                "date": day.isoformat(),
                "views": (rr.views if rr else 0) if is_covered else None,
                "unique_views": (rr.unique_views if rr else 0) if is_covered else None,
                "interests": (rr.interests if rr else 0) if is_covered else None,
                "orders": o["orders"] if o else 0,
                "sold_orders": o["sold"] if o else 0,
                "sales": {c: money(v) for c, v in o["sales"].items()} if o else {},
            }
        )

    return {
        "business_id": business.pk,
        "product": {
            "product_id": product.pk,
            "name": product.name,
            "currency": product.currency,
            "is_active": product.is_active,
        },
        "period": period_dict(date_from, date_to),
        "totals": {
            "views": views,
            "unique_views": unique_views,
            "saves": saves,
            "interests": interests["interests"],
            "orders": orders["orders"],
            "sold_orders": orders["confirmed"],
            "units": orders["units"],
            "sales": sales_by_currency(business, date_from, date_to, product),
            "conversion": (
                ratio(orders["confirmed"], unique_views)
                if cov["views_complete"]
                else None
            ),
        },
        "interested_customers": interests["customers"],
        "funnel": build_funnel(
            views=unique_views,
            interested=interests["customers"],
            ordering=orders["customers"],
            confirmed=orders["customers_confirmed"],
            completed=orders["customers_completed"],
            views_complete=cov["views_complete"],
        ),
        "trend": trend,
        "coverage": public_coverage(cov),
        "data_updated_at": iso(cov["updated"]),
        "orders_data_updated_at": iso(orders["updated"]),
    }


# ----------------------------------------------------------------------- content
def build_content_rows(business, kinds, date_from, date_to, cov):
    available = cov["views_available"]
    rolls = {}
    if available:
        grouped = (
            ContentDailyStats.objects.filter(
                business=business,
                content_kind__in=kinds,
                date__range=(date_from, date_to),
            )
            .values("content_kind", "object_id")
            .order_by()
            .annotate(
                views=Sum("views"),
                unique_views=Sum("unique_views"),
                likes=Sum("likes"),
                comments=Sum("comments"),
                saves=Sum("saves"),
                shares=Sum("shares"),
            )
        )
        rolls = {(r["content_kind"], r["object_id"]): r for r in grouped}

    rows = []
    for kind, model in (("post", Post), ("reel", Reel)):
        if kind not in kinds:
            continue
        items = model.objects.filter(business=business).values(
            "id", "caption", "status", "created_at"
        )
        for item in items:
            rr = rolls.get((kind, item["id"]))

            def val(key, rr=rr):
                if not available:
                    return None
                return rr[key] if rr else 0

            rows.append(
                {
                    "content_type": kind,
                    "content_id": item["id"],
                    "caption_preview": (item["caption"] or "")[:80],
                    "status": item["status"],
                    "created_at": iso(item["created_at"]),
                    "views": val("views"),
                    "unique_views": val("unique_views"),
                    "likes": val("likes"),
                    "comments": val("comments"),
                    "saves": val("saves"),
                    "in_app_shares": val("shares"),
                    "engagement_rate": engagement_rate(
                        val("likes"),
                        val("comments"),
                        val("saves"),
                        val("shares"),
                        val("unique_views"),
                    ),
                }
            )
    rows.sort(key=lambda r: r["created_at"] or "", reverse=True)
    return rows


def get_content_item(business, kind, content_id):
    if kind == "post":
        model = Post
    elif kind == "reel":
        model = Reel
    else:
        return None
    return model.objects.filter(pk=content_id, business=business).first()


def build_content_detail(business, kind, item, date_from, date_to):
    cov = rollup_coverage(business, date_from, date_to)
    available = cov["views_available"]
    covered = covered_days(business, date_from, date_to)
    base = ContentDailyStats.objects.filter(
        business=business,
        content_kind=kind,
        object_id=item.pk,
        date__range=(date_from, date_to),
    )
    agg = base.aggregate(
        views=Sum("views"),
        unique_views=Sum("unique_views"),
        likes=Sum("likes"),
        comments=Sum("comments"),
        saves=Sum("saves"),
        shares=Sum("shares"),
    )

    def total(key):
        return (agg[key] or 0) if available else None

    rolls = {r.date: r for r in base}
    trend = []
    for day in _days(date_from, date_to):
        rr = rolls.get(day)
        is_covered = day in covered

        def val(field, rr=rr, is_covered=is_covered):
            if not is_covered:
                return None
            return getattr(rr, field) if rr else 0

        trend.append(
            {
                "date": day.isoformat(),
                "views": val("views"),
                "unique_views": val("unique_views"),
                "likes": val("likes"),
                "comments": val("comments"),
                "saves": val("saves"),
                "in_app_shares": val("shares"),
                "engagement_rate": engagement_rate(
                    val("likes"),
                    val("comments"),
                    val("saves"),
                    val("shares"),
                    val("unique_views"),
                ),
            }
        )

    return {
        "business_id": business.pk,
        "content": {
            "content_type": kind,
            "content_id": item.pk,
            "caption_preview": (item.caption or "")[:80],
            "status": item.status,
            "created_at": iso(item.created_at),
        },
        "period": period_dict(date_from, date_to),
        "internal": {
            "source": "internal_events",
            "totals": {
                "views": total("views"),
                "unique_views": total("unique_views"),
                "likes": total("likes"),
                "comments": total("comments"),
                "saves": total("saves"),
                "in_app_shares": total("shares"),
                "engagement_rate": engagement_rate(
                    total("likes"),
                    total("comments"),
                    total("saves"),
                    total("shares"),
                    total("unique_views"),
                ),
            },
            "trend": trend,
            "notes": {
                "in_app_shares": (
                    "In-app share events only; they do not prove any external view."
                ),
                "reach": ("Not defined: there is no reliable source for it."),
            },
        },
        "external": {
            "metrics": latest_snapshots(business, kind, item.pk),
            "note": (
                "Owner-entered numbers from other platforms. Never merged with the "
                "internal numbers above."
            ),
        },
        "coverage": public_coverage(cov),
        "data_updated_at": iso(cov["updated"]),
    }


# ---------------------------------------------------------------- orders summary
def build_orders_summary(business, date_from, date_to, top):
    queryset = _order_queryset(business, date_from, date_to)
    totals = order_stage_counts(business, date_from, date_to)
    by_status_counts = {
        r["status"]: r["n"]
        for r in queryset.values("status").order_by().annotate(n=Count("id"))
    }
    per_day = _orders_by_day(queryset)
    trend = []
    for day in _days(date_from, date_to):
        o = per_day.get(day)
        trend.append(
            {
                "date": day.isoformat(),
                "orders": o["orders"] if o else 0,
                "sold_orders": o["sold"] if o else 0,
                "sales": {c: money(v) for c, v in o["sales"].items()} if o else {},
            }
        )
    top_rows = (
        queryset.filter(status__in=C.SALES_STATUSES)
        .values("product_id", "product__name", "currency")
        .order_by()
        .annotate(
            sales_value=Sum("total_amount"),
            sold_orders=Count("id"),
            units=Sum("quantity"),
        )
        .order_by("-sales_value", "product_id")[:top]
    )
    return {
        "business_id": business.pk,
        "period": period_dict(date_from, date_to),
        "orders": totals["orders"],
        "units": totals["units"],
        "by_status": [
            {"status": s, "orders": by_status_counts.get(s, 0)}
            for s in C.ALL_ORDER_STATUSES
        ],
        "sales": sales_by_currency(business, date_from, date_to),
        "sales_definition": (
            "Sales Value = confirmed + processing + shipped + delivered orders, "
            "per currency. pending and cancelled are excluded."
        ),
        "trend": trend,
        "top_products": [
            {
                "product_id": r["product_id"],
                "name": r["product__name"],
                "currency": r["currency"],
                "sales_value": money(r["sales_value"]),
                "sold_orders": r["sold_orders"],
                "units": r["units"] or 0,
            }
            for r in top_rows
        ],
        "data_updated_at": iso(totals["updated"]),
    }
