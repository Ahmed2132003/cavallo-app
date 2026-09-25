"""
Part P-059 — Home Feed API.

GET /api/v1/feed/home/?cursor=<opaque>&page_size=<int>

Authenticated only — there is no meaningful "home feed" without a user
to resolve follows/exclusions for. A malformed cursor or an
out-of-range page_size is a 400, not a 500: get_home_feed() raises
ValueError for both (InvalidCursorError included, since it subclasses
ValueError), and this view is the one place that catches it and turns
it into a DRF ValidationError.
"""

from rest_framework.exceptions import ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from feed.serializers import FeedItemSerializer
from feed.services import get_home_feed

DEFAULT_PAGE_SIZE = 20


class HomeFeedView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = FeedItemSerializer

    def get(self, request):
        cursor = request.query_params.get("cursor") or None
        page_size_raw = request.query_params.get("page_size")
        if page_size_raw is not None:
            try:
                page_size = int(page_size_raw)
            except ValueError:
                raise ValidationError({"page_size": "Must be an integer."})
        else:
            page_size = DEFAULT_PAGE_SIZE

        try:
            result = get_home_feed(request.user, cursor=cursor, page_size=page_size)
        except ValueError as exc:
            # Covers both InvalidCursorError (bad `cursor`) and an
            # out-of-range `page_size` — get_home_feed() raises
            # ValueError for both, deliberately without distinguishing
            # them in the message (see feed/cursor.py's own docstring
            # on not leaking why a forged cursor failed).
            raise ValidationError({"detail": str(exc)})

        serializer = self.get_serializer(result["items"], many=True)
        return Response(
            {"items": serializer.data, "next_cursor": result["next_cursor"]}
        )
