"""
Part P-064 STEP 1 — Search query/merge service.

Architecture rules
------------------
- Product is read through `Product.objects` only (SoftDeleteModel's
  manager already excludes soft-deleted rows), further narrowed to
  `is_active=True` — the exact same public-visibility rule
  ProductPublicListView (P-032) already uses. Product is NOT a
  Moderatable content type (see products/models.py), so there is no
  `published_objects`-style manager to route through here.
- BusinessProfile is read through `BusinessProfile.objects` only
  (same SoftDeleteModel exclusion). There is currently no separate
  "approved"/"active" gate on BusinessProfile beyond soft-delete —
  confirmed directly against businesses/models.py before writing this,
  per this part's own "BEFORE CODING" instruction.
- `Product` carries no `country`/`city` of its own (confirmed against
  products/models.py — contrary to the original part text's
  assumption, and explicitly flagged as a carry-forward note at the
  end of P-109's progress entry). Those two filters reach Product only
  through `business__country` / `business__city`.
- Per this part's own Detailed Implementation section: `category`
  filters Product only (BusinessProfile also has its own `category`
  FK from P-026, but the part text does not list `category` among the
  filters that apply to BusinessProfile — flagged in this part's
  handoff, not silently assumed either way).
- `min_price`/`max_price` filter `Product.price` only, and are never
  named or documented anywhere as anything resembling a transactional
  range (Section 20 / P-031's own convention).

Merge strategy and its trade-off (MVP, documented on purpose —
same trade-off feed/services.py already accepted for Post+Reel)
-----------------------------------------------------------------
BusinessProfile and Product live in different tables, so one ORM
queryset cannot order them together. For each request we fetch at
most `page_size` rows from EACH model (already in that model's own
total order), merge them in Python and keep the first `page_size`.
This is exact, not approximate: to get the top-k of the union of two
already-sorted streams, no stream can contribute more than k items to
that top-k — so fetching `page_size` from each is always enough,
whether resuming from the top or from a cursor. The cost is up to
2 x page_size rows read per request, matching feed's own accepted
Stage-1 trade-off (architecture Section 2).

Ordering is a TOTAL order in both modes, so a cursor can never land in
an ambiguous spot:

    relevance mode (q given):  (is_featured, rank, content-type rank, id), all descending
    recency mode   (no q):     (is_featured, created_at, content-type rank, id), all descending

No caching (deliberately, unlike Feed)
---------------------------------------
Feed's own P-060 added Redis caching because the Home Feed is the same
expensive query repeated for the same user very frequently. Search
requests are highly parameter-dependent (arbitrary q/filter
combinations per request) and already served off two GIN full-text
indexes (P-063) plus this part's own filter columns, so a cache here
would have a low hit rate for real added complexity. Flagged per this
part's own "flag rather than silently add/omit" instruction — revisit
if real usage data says otherwise.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F, Q, QuerySet

from businesses.models import BusinessProfile
from products.models import Product

from search.cursor import (
    CONTENT_TYPE_BUSINESS,
    CONTENT_TYPE_PRODUCT,
    CONTENT_TYPE_RANK,
    MODE_RECENCY,
    MODE_RELEVANCE,
    SearchCursor,
    decode_cursor,
    encode_cursor,
)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


@dataclass(frozen=True)
class SearchFilters:
    """Already-parsed filter values. The view (STEP 2) is the layer
    that turns raw query params into this — nothing here ever touches
    request.GET directly, so this module stays independently testable.
    """

    category_id: Optional[int] = None
    country: Optional[str] = None
    city: Optional[str] = None
    business_type: Optional[str] = None
    min_rating: Optional[Decimal] = None
    featured_only: bool = False
    min_price: Optional[Decimal] = None
    max_price: Optional[Decimal] = None


@dataclass(frozen=True)
class SearchResult:
    """One merged result: a BusinessProfile or a Product, tagged with
    its result_type and the two values the total order sorts on."""

    content_type: str  # "business" | "product"
    obj: Any
    is_featured: bool
    secondary: Any  # float (relevance mode) or datetime (recency mode)

    @property
    def id(self) -> int:
        return self.obj.pk

    def to_cursor(self, mode: str) -> SearchCursor:
        return SearchCursor(
            mode=mode,
            is_featured=self.is_featured,
            secondary=self.secondary,
            content_type=self.content_type,
            object_id=self.id,
        )


def _sort_key(entry: SearchResult):
    """Total order of the merged list (sort with reverse=True)."""
    return (
        entry.is_featured,
        entry.secondary,
        CONTENT_TYPE_RANK[entry.content_type],
        entry.id,
    )


def _rows_after(is_featured_field: str, secondary_field: str, *, content_type: str, cursor: SearchCursor) -> Q:
    """
    Rows of `content_type` that come strictly AFTER `cursor` in the
    descending (is_featured, secondary, content-type rank, id) order —
    same construction as feed/services.py's `_backfill_rows_after`,
    generalised so `secondary_field` can be an annotated rank OR a
    plain `created_at`, and `is_featured_field` can be a direct field
    (BusinessProfile.is_featured) or a join (Product.business__is_featured).
    """
    rank = CONTENT_TYPE_RANK[content_type]
    cursor_rank = CONTENT_TYPE_RANK[cursor.content_type]
    if rank < cursor_rank:
        same_secondary_tiebreak = Q(**{f"{secondary_field}__lte": cursor.secondary})
    elif rank > cursor_rank:
        same_secondary_tiebreak = Q(**{f"{secondary_field}__lt": cursor.secondary})
    else:
        same_secondary_tiebreak = Q(**{f"{secondary_field}__lt": cursor.secondary}) | Q(
            **{secondary_field: cursor.secondary, "id__lt": cursor.object_id}
        )

    if cursor.is_featured:
        return Q(**{is_featured_field: False}) | (
            Q(**{is_featured_field: True}) & same_secondary_tiebreak
        )
    return Q(**{is_featured_field: False}) & same_secondary_tiebreak


def build_business_queryset(filters: SearchFilters) -> QuerySet:
    """BusinessProfile rows matching every present filter that applies
    to businesses. `category` is deliberately NOT applied here — see
    module docstring."""
    qs = BusinessProfile.objects.all()
    if filters.country:
        qs = qs.filter(country=filters.country)
    if filters.city:
        qs = qs.filter(city=filters.city)
    if filters.business_type:
        qs = qs.filter(business_type=filters.business_type)
    if filters.min_rating is not None:
        qs = qs.filter(average_rating__gte=filters.min_rating)
    if filters.featured_only:
        qs = qs.filter(is_featured=True)
    return qs


def build_product_queryset(filters: SearchFilters) -> QuerySet:
    """Product rows matching every present filter that applies to
    products. `country`/`city` join through `business__...` since
    Product has no such fields of its own."""
    qs = Product.objects.filter(is_active=True)
    if filters.category_id is not None:
        qs = qs.filter(category_id=filters.category_id)
    if filters.country:
        qs = qs.filter(business__country=filters.country)
    if filters.city:
        qs = qs.filter(business__city=filters.city)
    if filters.min_price is not None:
        qs = qs.filter(price__gte=filters.min_price)
    if filters.max_price is not None:
        qs = qs.filter(price__lte=filters.max_price)
    if filters.featured_only:
        qs = qs.filter(business__is_featured=True)
    return qs


def _fetch_business_entries(filters, *, search_query, mode, cursor, limit):
    if limit <= 0:
        return []
    qs = build_business_queryset(filters)
    if search_query is not None:
        qs = qs.filter(search_vector=search_query).annotate(
            rank=SearchRank(F("search_vector"), search_query)
        )
        qs = qs.order_by("-is_featured", "-rank", "-id")
        secondary_field = "rank"
    else:
        qs = qs.order_by("-is_featured", "-created_at", "-id")
        secondary_field = "created_at"

    if cursor is not None:
        qs = qs.filter(
            _rows_after(
                "is_featured",
                secondary_field,
                content_type=CONTENT_TYPE_BUSINESS,
                cursor=cursor,
            )
        )

    entries = []
    for obj in qs[:limit]:
        secondary = obj.rank if mode == MODE_RELEVANCE else obj.created_at
        entries.append(
            SearchResult(
                content_type=CONTENT_TYPE_BUSINESS,
                obj=obj,
                is_featured=obj.is_featured,
                secondary=secondary,
            )
        )
    return entries


def _fetch_product_entries(filters, *, search_query, mode, cursor, limit):
    if limit <= 0:
        return []
    qs = build_product_queryset(filters)
    if search_query is not None:
        qs = qs.filter(search_vector=search_query).annotate(
            rank=SearchRank(F("search_vector"), search_query)
        )
        qs = qs.order_by("-business__is_featured", "-rank", "-id")
        secondary_field = "rank"
    else:
        qs = qs.order_by("-business__is_featured", "-created_at", "-id")
        secondary_field = "created_at"

    if cursor is not None:
        qs = qs.filter(
            _rows_after(
                "business__is_featured",
                secondary_field,
                content_type=CONTENT_TYPE_PRODUCT,
                cursor=cursor,
            )
        )

    qs = qs.select_related("business")
    entries = []
    for obj in qs[:limit]:
        secondary = obj.rank if mode == MODE_RELEVANCE else obj.created_at
        entries.append(
            SearchResult(
                content_type=CONTENT_TYPE_PRODUCT,
                obj=obj,
                is_featured=obj.business.is_featured,
                secondary=secondary,
            )
        )
    return entries


def get_search_results(
    filters: SearchFilters,
    *,
    q: Optional[str] = None,
    cursor: Optional[str] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict:
    """
    The merged, paginated search result set.

    Returns {"items": [SearchResult, ...], "next_cursor": str|None}.

    Raises InvalidCursorError (a ValueError) if `cursor` is malformed
    or was issued in the wrong mode, and ValueError if `page_size` is
    out of range — both are caller input errors for the view (STEP 2)
    to turn into a 400, exactly like feed/services.get_home_feed()
    already does for its own cursor.
    """
    if not isinstance(page_size, int) or page_size <= 0 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

    normalized_q = q.strip() if q else ""
    mode = MODE_RELEVANCE if normalized_q else MODE_RECENCY
    search_query = SearchQuery(normalized_q) if normalized_q else None

    decoded_cursor = decode_cursor(cursor, expected_mode=mode) if cursor else None

    business_entries = _fetch_business_entries(
        filters,
        search_query=search_query,
        mode=mode,
        cursor=decoded_cursor,
        limit=page_size,
    )
    product_entries = _fetch_product_entries(
        filters,
        search_query=search_query,
        mode=mode,
        cursor=decoded_cursor,
        limit=page_size,
    )

    merged = business_entries + product_entries
    merged.sort(key=_sort_key, reverse=True)
    items = merged[:page_size]

    next_cursor = None
    if len(items) >= page_size:
        next_cursor = encode_cursor(items[-1].to_cursor(mode))

    return {"items": items, "next_cursor": next_cursor}