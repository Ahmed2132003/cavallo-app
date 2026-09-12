"""
Redis-backed cache framework convenience wrapper (Part P-014).

Architecture Section 16 specifies Redis-backed caching for the Feed,
Business Profile, and Categories endpoints, each with its own TTL. This
module is the single call point every future caching call site should
use instead of calling Django's cache API (``cache.get``/``cache.set``)
directly with ad hoc key strings — that keeps every cached value's TTL
auditable against Section 16 in one place, and makes future changes to
caching behavior (logging, invalidation strategy, etc.) a one-file change
instead of a project-wide grep.

Key-naming convention — every call site MUST follow this:

    "{domain}:{identifier}:{qualifier}"

    Examples:
        "feed:42:page1"          — user 42's feed, page 1
        "business_profile:17"    — business profile id 17 (no qualifier)
        "categories:tree"        — the full categories tree

TTL values — source of truth is architecture Section 16's table. Do not
invent a TTL; use the value assigned to whatever you're caching there:

    Feed                60–120 seconds
    Business Profile    5 minutes   (300 seconds)
    Categories          ~1 hour     (3600 seconds)

This module only provides the mechanism (this part's scope). No specific
cached view/endpoint is implemented here — Feed/Business Profile caching
lands in Phase 10/Phase 4 respectively, both by calling
``cache_get_or_set`` with the key convention and TTL above.
"""

from django.core.cache import cache


def cache_get_or_set(key, compute_fn, ttl_seconds):
    """
    Return the cached value for `key`, computing and storing it on a miss.

    Args:
        key: cache key string. Must follow the "{domain}:{identifier}:
            {qualifier}" convention documented in this module's
            docstring (qualifier is optional).
        compute_fn: zero-argument callable, invoked only on a cache
            miss. Its return value is stored in the cache and returned.
        ttl_seconds: int, seconds until the cached value expires.

    Returns:
        The cached value on a hit; otherwise the freshly computed
        return value of `compute_fn()` (which is stored before being
        returned).

    Note on concurrency: this is a plain get-then-set, not an atomic
    check-and-set. Under concurrent cache-miss requests for the same
    key, `compute_fn` can run more than once and the last write wins —
    the same trade-off Django's own `cache.get_or_set()` makes. Don't
    use this where exactly-once execution matters (use a Celery task
    plus an explicit lock for that instead).

    Note on falsy/None values: like Django's own cache API, storing
    `None` is indistinguishable from a miss on the next call, so a
    `compute_fn` that legitimately returns `None` will be re-run on
    every call rather than getting cached. Callers whose computed value
    can legitimately be `None` should wrap it (e.g. in a small dict or
    sentinel) before caching, or use Django's cache API directly.
    """
    value = cache.get(key)
    if value is not None:
        return value

    value = compute_fn()
    cache.set(key, value, ttl_seconds)
    return value