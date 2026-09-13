"""
Request-id context variable + logging filter (Part P-015).

Architecture Section 24 asks for structured (JSON) request logging with
a request_id that traces a single request across every log line it
produces. The pieces are split across two small, focused modules:

    core/middleware.py    -- RequestIdMiddleware assigns/propagates the
                              request_id for the lifetime of one request.
    core/logging_utils.py -- this file. Holds the ContextVar the
                              middleware writes to, and the logging
                              Filter that reads it back into every log
                              record passing through a handler this
                              filter is attached to (see LOGGING in
                              config/settings/base.py).

Using contextvars.ContextVar rather than a plain thread-local is
deliberate: threading.local() breaks under async views/Channels
consumers (a coroutine isn't guaranteed to keep running on the same
thread), while ContextVar is copied correctly across await points and
into any task spawned from the current context. This repo doesn't have
async views yet, but P-000's ASGI_APPLICATION wiring and Channels
being installed mean it will -- no reason to pick the one primitive
that's already known to not survive that.
"""

import logging
from contextvars import ContextVar

# Default outside of any request context (e.g. a Celery task, a
# management command, or the moment before RequestIdMiddleware has run)
# so the filter below never raises LookupError -- it just tags those
# log lines with a clearly-non-request value instead of crashing.
request_id_ctx_var: ContextVar[str] = ContextVar("request_id", default="no-request")


class RequestIdFilter(logging.Filter):
    """
    Logging filter that stamps every record with the current request_id.

    Attach this to a handler (see config/settings/base.py's LOGGING
    dict) rather than to a logger, so it runs once per emitted record
    right before formatting -- this is what lets %(request_id)s be used
    safely in a formatter's format string for every record that reaches
    that handler, request-scoped or not.
    """

    def filter(self, record):
        record.request_id = request_id_ctx_var.get()
        return True
