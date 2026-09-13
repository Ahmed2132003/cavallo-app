"""
RequestIdMiddleware (Part P-015).

Assigns a request_id (a UUID4, unless the caller already supplied one)
to every request, makes it available to every log line emitted while
that request is being processed (via the ContextVar in
core/logging_utils.py, read back by RequestIdFilter), echoes it back on
the response as X-Request-ID for client-side correlation, and logs one
structured "request completed" line per request so every request has
at least one real, request_id-tagged log entry -- not just the ones
that happen to hit an app-level logger call.

Naming/placement deviation from the part spec (flagged, not silently
changed): the spec's file list calls for apps/core/middleware.py. This
repo has no apps/ package -- Part P-011 established that every app
lives at the repo root (core/, not apps/core/), so this file is
core/middleware.py instead. Functionally identical to the spec, same
convention every part since P-011 has followed.
"""

import logging
import uuid

from core.logging_utils import request_id_ctx_var

logger = logging.getLogger(__name__)


class RequestIdMiddleware:
    """
    Django middleware assigning/propagating a per-request request_id.

    Honors an incoming X-Request-ID header when present (e.g. one set
    by an upstream load balancer or gateway) so a request's id stays
    consistent across every hop, rather than being regenerated at each
    one -- this is what P-015's spec calls out as needed "for future
    load-balancer correlation." Generates a fresh uuid4 otherwise.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get("HTTP_X_REQUEST_ID", "").strip()
        request_id = incoming or str(uuid.uuid4())

        token = request_id_ctx_var.set(request_id)
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            logger.info(
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status_code": response.status_code,
                },
            )
            return response
        finally:
            # Always reset, even on an exception propagating out of
            # get_response, so the ContextVar never leaks this
            # request's id into whatever runs next in this
            # thread/task (the next request, in dev's synchronous
            # runserver; a differently-scoped bit of code, under
            # ASGI/Channels).
            request_id_ctx_var.reset(token)
