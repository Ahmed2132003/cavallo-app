"""Views for Part P-109 - rate-business (upsert) + public ratings list.

Mirrors social/views.py's ``_get_business_or_404`` convention (a
plain DRF-NotFound helper, not a queryset-level 404) so a bad {id}
returns this project's standard {"error": {...}} envelope rather than
a raw Django Http404.
"""

from rest_framework import generics
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from businesses.models import BusinessProfile
from core.pagination import StandardCursorPagination
from ratings.models import Rating
from ratings.serializers import (
    RateBusinessResponseSerializer,
    RateBusinessSerializer,
    RatingSerializer,
)
from ratings.services import rate_business


def _get_business_or_404(pk):
    try:
        return BusinessProfile.objects.get(pk=pk)
    except BusinessProfile.DoesNotExist:
        raise NotFound("Business not found.")


class RateBusinessView(APIView):
    """POST /api/v1/businesses/{id}/rate/ - authenticated.

    Upsert (not a toggle): the first call creates the caller's Rating
    for this business, a later call from the SAME customer updates
    that same row (score/review_text) rather than creating a second
    one. Returns the business's freshly recomputed
    average_rating/ratings_count - see ratings.services.rate_business
    for why this is a full recompute, not an F()-increment.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        business = _get_business_or_404(pk)

        serializer = RateBusinessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        _rating, _created, average_rating, ratings_count = rate_business(
            customer=request.user,
            business=business,
            score=data["score"],
            review_text=data["review_text"],
        )

        response = RateBusinessResponseSerializer(
            {"average_rating": average_rating, "ratings_count": ratings_count}
        )
        return Response(response.data)


class RatingsListView(generics.ListAPIView):
    """GET /api/v1/businesses/{id}/ratings/ - public, paginated.

    Lists individual reviews for one business, newest first (matches
    ratings_rating_business_idx). No Flutter reviews-browsing screen
    is required by this part (flagged, optional, per this part's own
    Out of Scope note) - this endpoint exists so one can be added
    later without a further backend part.
    """

    serializer_class = RatingSerializer
    permission_classes = [AllowAny]
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        # StandardCursorPagination orders by "-created_at" (its own
        # `ordering` attribute), matching this queryset's plain
        # business filter - no extra .order_by() needed here since
        # Rating.Meta.ordering already defaults to the same "-created_at".
        return Rating.objects.filter(business_id=self.kwargs["pk"])
