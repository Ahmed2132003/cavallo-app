"""
Part P-064 STEP 2 — Search HTTP endpoint.

GET /api/v1/search/?q=&category=&country=&city=&business_type=&
    min_rating=&featured_only=&min_price=&max_price=&cursor=&page_size=

Public (AllowAny) — search is a discovery feature per this part's own
Scope section, unlike feed/views.py's HomeFeedView/DiscoverFeedView,
both of which require a user to resolve follows/exclusions for.
Search has no such per-user state to resolve, same reasoning as
businesses/views.py's BusinessProfilePublicView and
products/views.py's ProductPublicListView.

Every filter is optional and combinable (this part's own Detailed
Implementation section). Raw query params are parsed and validated
ONLY here — search.services.get_search_results() never touches
request.GET directly, matching feed/views.py's own separation of
concerns. A malformed value (non-numeric category/min_rating/
min_price/max_price, an unparseable featured_only, an unparseable
cursor, an out-of-range page_size) is a 400, never a 500.

Price-framing note (Section 20 / P-031's convention, carried forward
via P-034's precedent): the query params are named `min_price`/
`max_price`, never anything resembling a transactional range (e.g.
never "price_min_to_buy"), and the response is a plain serialized
Product via ProductSerializer — no added transactional wording
anywhere on this endpoint's surface.
"""

from decimal import Decimal, InvalidOperation

from rest_framework.exceptions import ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from search.serializers import SearchResultSerializer
from search.services import SearchFilters, get_search_results

DEFAULT_PAGE_SIZE = 20

_TRUE_VALUES = {"true", "1"}
_FALSE_VALUES = {"false", "0"}


def _parse_int(raw, field_name):
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError({field_name: "Must be an integer."})


def _parse_decimal(raw, field_name):
    try:
        return Decimal(raw)
    except (TypeError, InvalidOperation):
        raise ValidationError({field_name: "Must be a number."})


def _parse_bool(raw, field_name):
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValidationError({field_name: "Must be true or false."})


class SearchView(GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = SearchResultSerializer

    def get(self, request):
        params = request.query_params

        q = params.get("q") or None

        category_raw = params.get("category")
        category_id = _parse_int(category_raw, "category") if category_raw else None

        country = params.get("country") or None
        city = params.get("city") or None
        business_type = params.get("business_type") or None

        min_rating_raw = params.get("min_rating")
        min_rating = (
            _parse_decimal(min_rating_raw, "min_rating") if min_rating_raw else None
        )

        featured_only_raw = params.get("featured_only")
        featured_only = (
            _parse_bool(featured_only_raw, "featured_only")
            if featured_only_raw
            else False
        )

        min_price_raw = params.get("min_price")
        min_price = (
            _parse_decimal(min_price_raw, "min_price") if min_price_raw else None
        )

        max_price_raw = params.get("max_price")
        max_price = (
            _parse_decimal(max_price_raw, "max_price") if max_price_raw else None
        )

        filters = SearchFilters(
            category_id=category_id,
            country=country,
            city=city,
            business_type=business_type,
            min_rating=min_rating,
            featured_only=featured_only,
            min_price=min_price,
            max_price=max_price,
        )

        cursor = params.get("cursor") or None
        page_size_raw = params.get("page_size")
        page_size = (
            _parse_int(page_size_raw, "page_size")
            if page_size_raw is not None
            else DEFAULT_PAGE_SIZE
        )

        try:
            result = get_search_results(
                filters, q=q, cursor=cursor, page_size=page_size
            )
        except ValueError as exc:
            # Covers both InvalidCursorError (bad `cursor`) and an
            # out-of-range `page_size` — get_search_results() raises
            # ValueError for both, deliberately without distinguishing
            # them in the message (see search/cursor.py's own
            # docstring on not leaking why a forged cursor failed),
            # matching feed/views.py's own HomeFeedView convention.
            raise ValidationError({"detail": str(exc)})

        serializer = self.get_serializer(result["items"], many=True)
        return Response(
            {"items": serializer.data, "next_cursor": result["next_cursor"]}
        )