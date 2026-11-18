"""BI-02 dashboard APIs: overview, products, content, orders-summary, legacy daily."""

from datetime import timedelta

import pytest
from django.utils import timezone

from analytics import constants as C
from analytics import metrics
from analytics.tests.bi02_helpers import (
    DAY1,
    DAY2,
    DAY3,
    RANGE,
    at,
    build_scenario,
    make_business,
    make_content_daily,
    make_customer,
    make_order,
    make_product,
    mark_day,
    url,
)
from core.tests.sweep_factories import client_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def s():
    return build_scenario()


def get(s_or_business, suffix, params=None, user=None):
    business = getattr(s_or_business, "business", s_or_business)
    return client_for(user or business.user).get(url(business, suffix), params or {})


# ================================================================ overview
def test_overview_numbers_and_funnel(s):
    r = get(s, "overview/", RANGE)
    assert r.status_code == 200
    body = r.json()
    assert body["business_id"] == s.business.pk
    assert body["period"] == {
        "date_from": "2026-03-10",
        "date_to": "2026-03-11",
        "days": 2,
        "period": "custom",
    }
    o = body["overview"]
    assert set(metrics.SCALAR_KEYS) <= set(o)
    assert o["product_views"] == 19
    assert o["product_unique_views"] == 13
    assert o["interests"] == 3
    assert o["interested_customers"] == 3
    assert o["orders"] == 5
    assert o["pending_orders"] == 1
    assert o["confirmed_orders"] == 3
    assert o["completed_orders"] == 1
    assert o["cancelled_orders"] == 1
    assert o["units"] == 4
    assert o["sales"] == [
        {
            "currency": "EGP",
            "sales_value": "450.00",
            "sold_orders": 3,
            "units": 4,
            "aov": "150.00",
        }
    ]
    assert (o["content_views"], o["likes"], o["comments"]) == (0, 0, 0)

    funnel = body["funnel"]
    assert [(st["key"], st["count"]) for st in funnel["stages"]] == [
        ("views", 13),
        ("interests", 3),
        ("orders", 3),
        ("confirmed", 3),
        ("completed", 1),
    ]
    rates = [c["rate"] for c in funnel["conversions"]]
    assert rates == pytest.approx([3 / 13, 1.0, 1.0, 1 / 3], abs=1e-3)
    assert funnel["overall_rate"] == pytest.approx(1 / 13, abs=1e-3)
    assert [(c["from"], c["to"]) for c in funnel["conversions"]][0] == (
        "views",
        "interests",
    )

    assert body["coverage"] == {
        "days_in_range": 2,
        "rollup_days": 2,
        "views_available": True,
        "views_complete": True,
    }
    assert body["data_updated_at"].endswith("Z")
    assert body["orders_data_updated_at"].endswith("Z")


def test_overview_excludes_pending_and_cancelled_from_sales_but_counts_them(s):
    o = get(s, "overview/", RANGE).json()["overview"]
    assert o["pending_orders"] == 1 and o["cancelled_orders"] == 1
    assert o["sales"][0]["sales_value"] == "450.00"  # 50 + 75 not included


def test_overview_currencies_are_never_summed(s):
    p3 = make_product(s.business, currency="SAR")
    make_order(s.business, p3, s.c1, "confirmed", 1, "200.00", at(DAY1))
    sales = get(s, "overview/", RANGE).json()["overview"]["sales"]
    assert [x["currency"] for x in sales] == ["EGP", "SAR"]
    assert sales[1]["sales_value"] == "200.00"


def test_order_period_uses_utc_day_bounds(s):
    make_order(s.business, s.p1, s.c2, "confirmed", 1, "10.00", at(DAY1, 0, 0))
    make_order(s.business, s.p1, s.c2, "confirmed", 1, "10.00", at(DAY3, 0, 0))
    o = get(s, "overview/", RANGE).json()["overview"]
    assert o["orders"] == 6  # DAY1 00:00 included, DAY3 00:00 excluded


def test_overview_missing_view_data_is_null_not_zero():
    business = make_business()  # no rollup coverage at all
    product = make_product(business)
    make_order(business, product, make_customer(), "confirmed", 1, "10.00", at(DAY1))
    body = get(business, "overview/", RANGE).json()
    o = body["overview"]
    assert o["product_views"] is None
    assert o["product_unique_views"] is None
    assert o["content_views"] is None and o["likes"] is None
    assert o["orders"] == 1 and o["interests"] == 0  # real counts stay numbers
    assert body["funnel"]["stages"][0]["count"] is None
    assert all(c["rate"] is None for c in body["funnel"]["conversions"][:1])
    assert body["funnel"]["overall_rate"] is None
    assert body["coverage"]["views_available"] is False
    assert body["data_updated_at"] is None
    assert body["previous_period"] is None


def test_partial_coverage_is_reported_and_hides_view_rates(s):
    body = get(
        s, "overview/", {"date_from": "2026-03-10", "date_to": "2026-03-12"}
    ).json()
    assert body["coverage"]["views_complete"] is False
    assert body["coverage"]["rollup_days"] == 2
    assert body["overview"]["product_views"] == 19  # still summed from covered days
    assert body["funnel"]["conversions"][0]["rate"] is None
    assert body["funnel"]["overall_rate"] is None
    assert body["previous_period"] is None


def test_previous_period_comparison_when_both_windows_are_covered(s):
    mark_day(s.business, DAY1 - timedelta(days=2))
    mark_day(s.business, DAY1 - timedelta(days=1))
    prev = get(s, "overview/", RANGE).json()["previous_period"]
    assert prev["period"] == {
        "date_from": "2026-03-08",
        "date_to": "2026-03-09",
        "days": 2,
    }
    assert prev["overview"]["orders"] == 0
    assert prev["deltas"]["orders"] == {
        "current": 5,
        "previous": 0,
        "change": 5,
        "change_pct": None,  # no percentage on a zero base
    }
    assert prev["deltas"]["product_views"]["change"] == 19


def test_previous_period_is_null_when_previous_window_has_no_coverage(s):
    assert get(s, "overview/", RANGE).json()["previous_period"] is None


def test_default_date_to_and_period(s):
    body = get(s, "overview/").json()
    assert body["period"]["date_to"] == DAY2.isoformat()
    assert body["period"]["days"] == 30
    assert body["period"]["period"] == "30d"


def test_default_date_to_falls_back_to_yesterday_without_rollups():
    assert metrics.default_date_to(make_business()) == (
        timezone.localdate() - timedelta(days=1)
    )


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "bad"},
        {"period": "5d"},
        {"date_from": "2026-03-11", "date_to": "2026-03-10"},
    ],
)
def test_overview_bad_query_is_400(s, params):
    r = get(s, "overview/", params)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# ================================================================ products
def test_products_list_rows(s):
    body = get(s, "products/", RANGE).json()
    assert body["count"] == 2
    rows = {r["product_id"]: r for r in body["results"]}
    r1, r2 = rows[s.p1.pk], rows[s.p2.pk]
    assert (r1["views"], r1["unique_views"], r1["interests"], r1["saves"]) == (
        14,
        9,
        2,
        1,
    )
    assert (r1["orders"], r1["sold_orders"], r1["units"]) == (3, 3, 4)
    assert r1["sales"] == [{"currency": "EGP", "sales_value": "450.00"}]
    assert r1["conversion"] == pytest.approx(3 / 9, abs=1e-3)
    assert (r2["views"], r2["unique_views"], r2["interests"]) == (5, 4, 1)
    assert (r2["orders"], r2["sold_orders"], r2["units"], r2["sales"]) == (2, 0, 0, [])
    assert r2["conversion"] == 0.0
    assert all("_sales_sort" not in r for r in body["results"])
    assert body["coverage"]["views_complete"] is True


@pytest.mark.parametrize(
    "ordering,first",
    [
        ("-views", "p1"),
        ("views", "p2"),
        ("-sales", "p1"),
        ("sales", "p2"),
        ("-orders", "p1"),
        ("-interests", "p1"),
        ("conversion", "p2"),
        ("-conversion", "p1"),
        ("-unique_views", "p1"),
    ],
)
def test_products_ordering(s, ordering, first):
    params = dict(RANGE, ordering=ordering)
    results = get(s, "products/", params).json()["results"]
    assert results[0]["product_id"] == getattr(s, first).pk


def test_products_default_ordering_is_views_desc(s):
    results = get(s, "products/", RANGE).json()["results"]
    assert [r["product_id"] for r in results] == [s.p1.pk, s.p2.pk]


def test_products_pagination(s):
    page1 = get(s, "products/", dict(RANGE, page_size=1)).json()
    assert (page1["count"], page1["page"], page1["total_pages"]) == (2, 1, 2)
    assert len(page1["results"]) == 1
    page2 = get(s, "products/", dict(RANGE, page_size=1, page=2)).json()
    assert page2["results"][0]["product_id"] == s.p2.pk


@pytest.mark.parametrize(
    "params",
    [{"ordering": "bogus"}, {"page_size": 101}, {"page_size": 0}, {"page": 0}],
)
def test_products_bad_query_is_400(s, params):
    assert get(s, "products/", dict(RANGE, **params)).status_code == 400


def test_products_without_coverage_have_null_views_and_conversion():
    business = make_business()
    product = make_product(business)
    make_order(business, product, make_customer(), "confirmed", 1, "10.00", at(DAY1))
    row = get(business, "products/", RANGE).json()["results"][0]
    assert row["views"] is None and row["unique_views"] is None
    assert row["conversion"] is None
    assert row["orders"] == 1 and row["sold_orders"] == 1


def test_products_list_only_contains_own_products(s):
    other = make_business()
    make_product(other)
    ids = {r["product_id"] for r in get(s, "products/", RANGE).json()["results"]}
    assert ids == {s.p1.pk, s.p2.pk}


def test_product_detail_totals_funnel_and_trend(s):
    body = get(s, f"products/{s.p1.pk}/", RANGE).json()
    assert body["product"]["product_id"] == s.p1.pk
    assert body["period"]["period"] == "custom"
    t = body["totals"]
    assert (t["views"], t["unique_views"], t["saves"], t["interests"]) == (14, 9, 1, 2)
    assert (t["orders"], t["sold_orders"], t["units"]) == (3, 3, 4)
    assert t["sales"] == [
        {
            "currency": "EGP",
            "sales_value": "450.00",
            "sold_orders": 3,
            "units": 4,
            "aov": "150.00",
        }
    ]
    assert t["conversion"] == pytest.approx(3 / 9, abs=1e-3)
    assert body["interested_customers"] == 2
    assert [(x["key"], x["count"]) for x in body["funnel"]["stages"]] == [
        ("views", 9),
        ("interests", 2),
        ("orders", 3),
        ("confirmed", 3),
        ("completed", 1),
    ]
    trend = {x["date"]: x for x in body["trend"]}
    assert len(trend) == 2
    d1, d2 = trend["2026-03-10"], trend["2026-03-11"]
    assert (d1["views"], d1["unique_views"], d1["orders"], d1["sold_orders"]) == (
        10,
        6,
        2,
        2,
    )
    assert d1["sales"] == {"EGP": "350.00"}
    assert (d2["views"], d2["orders"], d2["sold_orders"]) == (4, 1, 1)
    assert d2["sales"] == {"EGP": "100.00"}


def test_product_detail_uncovered_day_is_null_but_orders_are_real(s):
    body = get(
        s, f"products/{s.p1.pk}/", {"date_from": "2026-03-10", "date_to": "2026-03-12"}
    ).json()
    day3 = {x["date"]: x for x in body["trend"]}["2026-03-12"]
    assert day3["views"] is None and day3["unique_views"] is None
    assert day3["orders"] == 0


def test_product_detail_of_another_business_is_404(s):
    other = make_business()
    foreign = make_product(other)
    assert get(s, f"products/{foreign.pk}/", RANGE).status_code == 404
    assert get(s, "products/999999/", RANGE).status_code == 404


# ================================================================= content
@pytest.fixture
def content(s):
    make_content_daily(
        "post",
        s.post,
        DAY1,
        views=10,
        unique_views=5,
        likes=2,
        comments=1,
        saves=1,
        shares=1,
    )
    make_content_daily("reel", s.reel, DAY1, views=8, unique_views=0, likes=3)
    return s


def test_content_list_rows_and_engagement_rate(content):
    body = get(content, "content/", RANGE).json()
    assert body["type"] == "all"
    rows = {(r["content_type"], r["content_id"]): r for r in body["results"]}
    post = rows[("post", content.post.pk)]
    reel = rows[("reel", content.reel.pk)]
    assert (post["views"], post["unique_views"], post["likes"]) == (10, 5, 2)
    assert (post["comments"], post["saves"], post["in_app_shares"]) == (1, 1, 1)
    assert post["engagement_rate"] == 1.0
    assert post["caption_preview"] == "bi02 post"
    assert reel["engagement_rate"] is None  # unique views 0 -> null, not 0


def test_content_list_default_and_null_last_ordering(content):
    default = get(content, "content/", RANGE).json()["results"]
    assert default[0]["content_type"] == "post"  # views 10 > 8
    for ordering in ("-engagement_rate", "engagement_rate"):
        rows = get(content, "content/", dict(RANGE, ordering=ordering)).json()[
            "results"
        ]
        assert rows[-1]["engagement_rate"] is None


def test_content_list_type_filter(content):
    only_posts = get(content, "content/", dict(RANGE, type="post")).json()
    only_reels = get(content, "content/", dict(RANGE, type="reel")).json()
    assert {r["content_type"] for r in only_posts["results"]} == {"post"}
    assert {r["content_type"] for r in only_reels["results"]} == {"reel"}
    assert get(content, "content/", dict(RANGE, type="story")).status_code == 400


def test_content_without_coverage_is_null():
    business = make_business()
    from analytics.tests.bi02_helpers import make_post

    make_post(business)
    row = get(business, "content/", RANGE).json()["results"][0]
    assert row["views"] is None and row["likes"] is None
    assert row["engagement_rate"] is None


def test_content_detail_trend_and_notes(content):
    body = get(content, f"content/post/{content.post.pk}/", RANGE).json()
    assert body["content"]["content_type"] == "post"
    internal = body["internal"]
    assert internal["source"] == "internal_events"
    assert internal["totals"]["views"] == 10
    assert internal["totals"]["engagement_rate"] == 1.0
    assert "reach" in internal["notes"] and "in_app_shares" in internal["notes"]
    trend = {x["date"]: x for x in internal["trend"]}
    assert trend["2026-03-10"]["engagement_rate"] == 1.0
    assert trend["2026-03-11"]["views"] == 0  # covered day, no activity
    assert trend["2026-03-11"]["engagement_rate"] is None
    assert body["external"]["metrics"] == []


def test_content_detail_unknown_type_or_foreign_item_is_404(content):
    other = make_business()
    from analytics.tests.bi02_helpers import make_post

    foreign = make_post(other)
    assert get(content, f"content/story/{content.post.pk}/", RANGE).status_code == 404
    assert get(content, f"content/post/{foreign.pk}/", RANGE).status_code == 404
    assert get(content, "content/post/999999/", RANGE).status_code == 404
    assert (
        get(content, f"content/reel/{content.post.pk + 1000}/", RANGE).status_code
        == 404
    )


# ============================================================ orders summary
def test_orders_summary(s):
    body = get(s, "orders-summary/", RANGE).json()
    assert body["orders"] == 5 and body["units"] == 4
    assert [x["status"] for x in body["by_status"]] == list(C.ALL_ORDER_STATUSES)
    assert {x["status"]: x["orders"] for x in body["by_status"]} == {
        "pending": 1,
        "confirmed": 1,
        "processing": 1,
        "shipped": 0,
        "delivered": 1,
        "cancelled": 1,
    }
    assert body["sales"] == [
        {
            "currency": "EGP",
            "sales_value": "450.00",
            "sold_orders": 3,
            "units": 4,
            "aov": "150.00",
        }
    ]
    assert "pending and cancelled are excluded" in body["sales_definition"]
    trend = {x["date"]: x for x in body["trend"]}
    assert trend["2026-03-10"] == {
        "date": "2026-03-10",
        "orders": 2,
        "sold_orders": 2,
        "sales": {"EGP": "350.00"},
    }
    assert trend["2026-03-11"]["orders"] == 3
    assert trend["2026-03-11"]["sold_orders"] == 1
    assert body["top_products"] == [
        {
            "product_id": s.p1.pk,
            "name": s.p1.name,
            "currency": "EGP",
            "sales_value": "450.00",
            "sold_orders": 3,
            "units": 4,
        }
    ]
    assert body["data_updated_at"].endswith("Z")


def test_orders_summary_top_limit_and_validation(s):
    p3 = make_product(s.business, currency="SAR")
    make_order(s.business, p3, s.c1, "confirmed", 1, "200.00", at(DAY1))
    top1 = get(s, "orders-summary/", dict(RANGE, top=1)).json()["top_products"]
    assert [t["product_id"] for t in top1] == [s.p1.pk]
    top5 = get(s, "orders-summary/", dict(RANGE, top=5)).json()["top_products"]
    assert [t["product_id"] for t in top5] == [s.p1.pk, p3.pk]
    for bad in (0, 21):
        assert get(s, "orders-summary/", dict(RANGE, top=bad)).status_code == 400


# ================================================================ legacy API
def test_legacy_daily_endpoint_is_unchanged_by_bi_columns(s):
    r = get(s, "daily/")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"next", "previous", "results"}
    assert set(body["results"][0]) == {
        "date",
        "new_followers",
        "total_likes_received",
        "total_comments_received",
        "total_story_views",
        "new_ratings_count",
        "average_rating_snapshot",
        "active_products_count",
        "published_posts_count",
        "published_reels_count",
    }
