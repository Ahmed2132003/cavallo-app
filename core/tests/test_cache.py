"""
Tests for core.cache.cache_get_or_set (Part P-014).

Per this part's Testing note: run against the real Compose Redis service,
not a mock — the isolated cache DB index (REDIS_CACHE_DB, config/settings/
base.py) is exactly what's being exercised here, so a mock would prove
nothing about the actual DB-index separation from Celery's broker.
"""

import time

from django.core.cache import cache

from core.cache import cache_get_or_set


def setup_function(_):
    """Start every test from a clean cache DB — avoids cross-test leakage
    on shared keys within the same Redis cache DB index."""
    cache.clear()


def test_cache_get_or_set_hits_on_second_call():
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return "computed-value"

    first = cache_get_or_set("core_test:hit_check", compute, ttl_seconds=30)
    second = cache_get_or_set("core_test:hit_check", compute, ttl_seconds=30)

    assert first == "computed-value"
    assert second == "computed-value"
    # compute_fn only ran once — the second call was served from cache.
    assert calls["count"] == 1


def test_cache_get_or_set_expires_after_ttl():
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return f"computed-{calls['count']}"

    first = cache_get_or_set("core_test:ttl_check", compute, ttl_seconds=1)
    time.sleep(1.5)  # Wait past the 1-second TTL.
    second = cache_get_or_set("core_test:ttl_check", compute, ttl_seconds=1)

    assert first == "computed-1"
    assert second == "computed-2"
    # compute_fn ran twice — the key had genuinely expired in Redis.
    assert calls["count"] == 2


def test_cache_get_or_set_keys_are_independent():
    def compute_a():
        return "value-a"

    def compute_b():
        return "value-b"

    a = cache_get_or_set("core_test:key_a", compute_a, ttl_seconds=30)
    b = cache_get_or_set("core_test:key_b", compute_b, ttl_seconds=30)

    assert a == "value-a"
    assert b == "value-b"