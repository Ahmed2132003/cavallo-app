from rest_framework import generics, permissions
from rest_framework.exceptions import NotFound, PermissionDenied

from analytics.models import BusinessDailyStats
from analytics.pagination import DailyStatsCursorPagination
from analytics.serializers import (
    BusinessDailyStatsSerializer,
    DailyStatsQuerySerializer,
)
from businesses.models import BusinessProfile


class BusinessDailyStatsListView(generics.ListAPIView):
    """
    Part P-084. GET /api/v1/analytics/business/{id}/daily/ — authenticated,
    OWNER-ONLY, read-only, cursor-paginated (newest day first).

    Same explicit object-level IDOR check used since P-026/P-032/P-049
    (see stories/views.py's StoryViewCountView): the business is looked up
    by the URL id, then ``business.user_id != request.user.id`` raises
    PermissionDenied inside the view rather than relying on
    permission_classes alone. Order of checks:
      401 (not authenticated) -> 404 (no such / soft-deleted business)
      -> 403 (not the owner) -> 400 (bad query params).
    Ownership is checked BEFORE query validation so a non-owner learns
    nothing about the endpoint beyond the 403.

    Optional inclusive range filters: ?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD.

    Serves precomputed BusinessDailyStats rows only (Architecture Section
    17): nothing is aggregated from the raw content tables on request.
    """

    serializer_class = BusinessDailyStatsSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = DailyStatsCursorPagination

    def get_queryset(self):
        business = BusinessProfile.objects.filter(pk=self.kwargs["pk"]).first()
        if business is None:
            raise NotFound("Business not found.")
        if business.user_id != self.request.user.id:
            raise PermissionDenied(
                "You do not have permission to view this business's analytics."
            )

        query = DailyStatsQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)

        queryset = BusinessDailyStats.objects.filter(business=business)
        date_from = query.validated_data.get("date_from")
        date_to = query.validated_data.get("date_to")
        if date_from:
            queryset = queryset.filter(date__gte=date_from)
        if date_to:
            queryset = queryset.filter(date__lte=date_to)
        return queryset