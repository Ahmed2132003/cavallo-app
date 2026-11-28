"""BI-02 pure-logic tests (no database)."""

import ast
import inspect
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from rest_framework import serializers

from analytics import constants as C
from analytics import external, metrics, tracking
from analytics.utils import day_bounds, iso, money, ratio
from orders.constants import SALES_VALUE_STATUSES, OrderStatus


# ------------------------------------------------------------- constants
def test_order_status_literals_match_orders_app():
    assert set(C.ALL_ORDER_STATUSES) == set(OrderStatus.values)
    assert C.SALES_STATUSES == SALES_VALUE_STATUSES
    for literal in (C.STATUS_PENDING, C.STATUS_CANCELLED, C.STATUS_COMPLETED):
        assert literal in OrderStatus.values


def test_sales_statuses_exclude_pending_and_cancelled():
    assert "pending" not in C.SALES_STATUSES
    assert "cancelled" not in C.SALES_STATUSES


# ---------------------------------------------------------- resolve_range
def test_period_7d_ends_at_default_to():
    df, dt, label = metrics.resolve_range({"period": "7d"}, date(2026, 3, 10))
    assert (df, dt, label) == (date(2026, 3, 4), date(2026, 3, 10), "7d")


def test_default_period_is_30d():
    df, dt, label = metrics.resolve_range({}, date(2026, 3, 30))
    assert label == "30d"
    assert (dt - df).days + 1 == 30


def test_date_from_wins_over_period_and_is_custom():
    df, dt, label = metrics.resolve_range(
        {"date_from": date(2026, 3, 1), "date_to": date(2026, 3, 5), "period": "90d"},
        date(2026, 3, 30),
    )
    assert (df, dt, label) == (date(2026, 3, 1), date(2026, 3, 5), "custom")


def test_date_to_defaults_when_missing():
    df, dt, label = metrics.resolve_range(
        {"date_from": date(2026, 3, 1)}, date(2026, 3, 9)
    )
    assert dt == date(2026, 3, 9)


def test_date_from_after_date_to_is_rejected():
    with pytest.raises(serializers.ValidationError) as exc:
        metrics.resolve_range(
            {"date_from": date(2026, 3, 5), "date_to": date(2026, 3, 1)},
            date(2026, 3, 9),
        )
    assert "date_from" in exc.value.detail


def test_range_cap_366_days():
    end = date(2026, 12, 31)
    start_ok = end - timedelta(days=C.MAX_RANGE_DAYS - 1)
    metrics.resolve_range({"date_from": start_ok, "date_to": end}, end)
    with pytest.raises(serializers.ValidationError):
        metrics.resolve_range(
            {"date_from": start_ok - timedelta(days=1), "date_to": end}, end
        )


def test_period_dict():
    data = metrics.period_dict(date(2026, 3, 10), date(2026, 3, 11), "custom")
    assert data == {
        "date_from": "2026-03-10",
        "date_to": "2026-03-11",
        "days": 2,
        "period": "custom",
    }
    assert "period" not in metrics.period_dict(date(2026, 3, 10), date(2026, 3, 11))


# ---------------------------------------------------------- ordering/paging
def test_parse_ordering_variants():
    allowed = metrics.PRODUCT_ORDERING
    assert metrics.parse_ordering(None, allowed, "-views") == ("views", True)
    assert metrics.parse_ordering("views", allowed, "-views") == ("views", False)
    assert metrics.parse_ordering("+views", allowed, "-views") == ("views", False)
    assert metrics.parse_ordering("-sales", allowed, "-views") == ("_sales_sort", True)


def test_parse_ordering_rejects_unknown_field():
    with pytest.raises(serializers.ValidationError) as exc:
        metrics.parse_ordering("-nope", metrics.PRODUCT_ORDERING, "-views")
    assert "ordering" in exc.value.detail


def test_sort_rows_puts_none_last_in_both_directions():
    rows = [{"v": 1}, {"v": None}, {"v": 3}]
    assert [r["v"] for r in metrics.sort_rows(rows, "v", True)] == [3, 1, None]
    assert [r["v"] for r in metrics.sort_rows(rows, "v", False)] == [1, 3, None]


def test_paginate_strips_private_keys_and_counts_pages():
    rows = [{"id": i, "_hidden": i} for i in range(5)]
    page = metrics.paginate(rows, 2, 2)
    assert page["count"] == 5
    assert page["total_pages"] == 3
    assert [r["id"] for r in page["results"]] == [2, 3]
    assert all("_hidden" not in r for r in page["results"])


def test_paginate_empty_and_out_of_range():
    assert metrics.paginate([], 1, 20)["total_pages"] == 1
    assert metrics.paginate([{"a": 1}], 5, 20)["results"] == []


# ------------------------------------------------------------------ utils
def test_ratio_never_returns_zero_for_missing_data():
    assert ratio(1, 0) is None
    assert ratio(None, 5) is None
    assert ratio(5, None) is None
    assert ratio(0, 4) == 0.0
    assert ratio(1, 3) == 0.3333


def test_money_formats_two_places_half_up():
    assert money(Decimal("1234.5")) == "1234.50"
    assert money(Decimal("0.005")) == "0.01"
    assert money(None) is None


def test_iso_uses_z_suffix():
    assert iso(None) is None
    assert iso(datetime(2026, 3, 10, 12, tzinfo=dt_timezone.utc)) == (
        "2026-03-10T12:00:00Z"
    )


def test_day_bounds_end_is_exclusive_next_day():
    start, end = day_bounds(date(2026, 3, 10), date(2026, 3, 11))
    assert start == datetime(2026, 3, 10, tzinfo=dt_timezone.utc)
    assert end == datetime(2026, 3, 12, tzinfo=dt_timezone.utc)


# ----------------------------------------------------- engagement and delta
def test_engagement_rate_formula_and_division_by_zero():
    assert metrics.engagement_rate(2, 1, 1, 1, 5) == 1.0
    assert metrics.engagement_rate(1, 1, 1, 1, 0) is None
    assert metrics.engagement_rate(None, 1, 1, 1, 5) is None
    assert metrics.engagement_rate(1, 1, 1, 1, None) is None


def test_delta_cases():
    assert metrics.delta(10, 4) == {
        "current": 10,
        "previous": 4,
        "change": 6,
        "change_pct": 1.5,
    }
    assert metrics.delta(None, 4)["change"] is None
    assert metrics.delta(4, None)["change_pct"] is None
    assert metrics.delta(5, 0)["change_pct"] is None
    assert metrics.delta(0, 0) == {
        "current": 0,
        "previous": 0,
        "change": 0,
        "change_pct": None,
    }


# ------------------------------------------------------------------ funnel
def test_funnel_complete_coverage():
    f = metrics.build_funnel(
        views=100,
        interested=20,
        ordering=10,
        confirmed=5,
        completed=0,
        views_complete=True,
    )
    assert [s["key"] for s in f["stages"]] == list(metrics.FUNNEL_STAGES)
    assert [c["rate"] for c in f["conversions"]] == [0.2, 0.5, 0.5, 0.0]
    assert f["overall_rate"] == 0.0


def test_funnel_incomplete_view_coverage_hides_view_rates_only():
    f = metrics.build_funnel(
        views=100,
        interested=20,
        ordering=10,
        confirmed=5,
        completed=2,
        views_complete=False,
    )
    assert f["conversions"][0]["rate"] is None
    assert f["conversions"][1]["rate"] == 0.5
    assert f["overall_rate"] is None


def test_funnel_missing_views_and_zero_previous_stage():
    f = metrics.build_funnel(
        views=None,
        interested=0,
        ordering=0,
        confirmed=0,
        completed=0,
        views_complete=False,
    )
    assert f["stages"][0]["count"] is None
    assert all(c["rate"] is None for c in f["conversions"])


# ---------------------------------------------------------------- tracking
def test_viewer_key_is_deterministic_hmac_hex_without_raw_ids():
    user = SimpleNamespace(pk=5, is_authenticated=True)
    k1 = tracking.build_viewer_key(user=user, anon_id=None)
    assert k1 == tracking.build_viewer_key(user=user, anon_id="ignored-for-users")
    assert len(k1) == 64
    assert "5" != k1 and "user:5" not in k1
    a1 = tracking.build_viewer_key(user=None, anon_id="install-0123456789abcdef")
    assert a1 != k1
    assert "install" not in a1


def test_viewer_key_changes_with_salt(settings):
    settings.ANALYTICS_VIEWER_KEY_SALT = "salt-one"
    first = tracking.build_viewer_key(user=None, anon_id="install-0123456789abcdef")
    settings.ANALYTICS_VIEWER_KEY_SALT = "salt-two"
    second = tracking.build_viewer_key(user=None, anon_id="install-0123456789abcdef")
    assert first != second


def test_reel_min_watch_ms_reads_settings(settings):
    settings.ANALYTICS_REEL_MIN_WATCH_MS = 1234
    assert tracking.reel_min_watch_ms() == 1234


# ---------------------------------------------------------------- external
class _DummyProvider(external.ExternalMetricsProvider):
    platform = "instagram"

    def fetch_metrics(self, *, business, content_type, content_id, external_reference):
        return []


def test_provider_interface_is_abstract():
    with pytest.raises(TypeError):
        external.ExternalMetricsProvider()


def test_provider_registry_accepts_known_platform_only(monkeypatch):
    monkeypatch.setattr(external, "_REGISTRY", {})
    assert external.registered_platforms() == []
    external.register_provider(_DummyProvider())
    assert external.registered_platforms() == ["instagram"]
    assert external.get_provider("instagram") is not None
    assert external.get_provider("tiktok") is None

    class Bad(_DummyProvider):
        platform = "myspace"

    with pytest.raises(ValueError):
        external.register_provider(Bad())


def test_external_module_never_imports_a_network_client():
    tree = ast.parse(inspect.getsource(external))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert not imported & {"requests", "urllib", "urllib3", "httpx", "socket", "http"}
