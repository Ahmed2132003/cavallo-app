"""
Health check view (Part P-015).

Naming/placement deviation from the part spec (flagged, not silently
changed): the spec's file list calls for apps/core/views.py. Same as
every part since P-011, this repo has no apps/ package -- the file is
core/views.py instead. Functionally identical to the spec.
"""

import logging
import uuid

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse

logger = logging.getLogger(__name__)

# Deliberately not run through core.cache.cache_get_or_set (Part
# P-014's normal, "always go through this" cache entry point): that
# helper returns a cached hit without ever touching Redis again on
# repeat calls, which would defeat the entire point of a connectivity
# probe that must exercise Redis on every single call. This is the
# one documented, deliberate exception to that convention -- called
# out explicitly here the same way P-011 called out the ModerationLog
# exception to SoftDeleteModel.
_HEALTH_CACHE_KEY = "core:health_check:probe"


def health_check(request):
    """
    Unauthenticated liveness/readiness probe (architecture Section 24).

    Verifies DB connectivity (connection.ensure_connection()) and Redis
    connectivity (a real cache.set/cache.get round trip) on every call.
    Returns 200 with {"status": "ok", "db": "ok", "redis": "ok"} when
    both are healthy, or 503 with the per-component "ok"/"error"
    breakdown when either fails.

    Never includes an exception message or stack trace in the response
    body -- this endpoint is hit anonymously by external infrastructure
    (load balancers, uptime monitors) and must not leak internals, even
    on failure. Full details still go to the server-side logs via
    logger.exception() for whoever is on call.

    Registered directly, unauthenticated, in config/urls.py -- there is
    no DRF permission_classes to bypass here since this is a plain
    Django view, not a DRF APIView; nothing in the project's default
    IsAuthenticated DRF setting applies to it.
    """
    db_ok = _check_database()
    redis_ok = _check_redis()
    healthy = db_ok and redis_ok

    body = {
        "status": "ok" if healthy else "unhealthy",
        "db": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "error",
    }
    return JsonResponse(body, status=200 if healthy else 503)


def _check_database():
    try:
        connection.ensure_connection()
        return True
    except Exception:
        logger.exception("Health check: database connectivity failed.")
        return False


def _check_redis():
    try:
        marker = str(uuid.uuid4())
        cache.set(_HEALTH_CACHE_KEY, marker, timeout=5)
        return cache.get(_HEALTH_CACHE_KEY) == marker
    except Exception:
        logger.exception("Health check: redis connectivity failed.")
        return False
