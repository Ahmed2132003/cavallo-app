"""
Shared pagination classes (Part P-011).

Architecture Section 9 point 7 requires cursor-based pagination — never
offset/limit — for the Feed and any other feed-like/list endpoint.
StandardCursorPagination is the required pagination class for those
endpoints; import and set it as `pagination_class` on the relevant
views/viewsets rather than defining a per-app cursor paginator.
"""

from rest_framework.pagination import CursorPagination


class StandardCursorPagination(CursorPagination):
    """
    Default cursor pagination for feed-like/list endpoints.

    Ordered by -created_at, which relies on every paginated model
    inheriting TimestampedModel. DRF's CursorPagination handles rows that
    share the same created_at value (e.g. millisecond-precision ties) via
    an internal offset component in the cursor itself (capped by DRF's
    default `offset_cutoff = 1000`) — no explicit "-id" tiebreaker needs
    to be listed in `ordering` for this to stay stable.
    """

    page_size = 20
    ordering = "-created_at"
    page_size_query_param = "page_size"
    max_page_size = 100
