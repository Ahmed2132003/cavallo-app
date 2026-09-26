"""
Part P-059 — Home Feed query service.

Architecture rules
------------------
- Post and Reel are read ONLY through `published_objects` (P-043), never
  through `.objects`: this is the Feed's own proof of respecting the
  central moderation-bypass mitigation from Phase 7.
- The feed is model-less: nothing here writes to the database.

Merge strategy and its trade-off (MVP, documented on purpose)
-------------------------------------------------------------
Post and Reel live in different tables, so one ORM queryset cannot
order them together. For each tier we fetch at most `limit` rows from
EACH model (already in that model's own order), merge them in Python
and keep the first `limit`. This is exact, not approximate: the first
`limit` items of the merged list can never need more than `limit` rows
from either model. The cost is up to 2 x limit rows read per tier per
request (2 queries), which does not scale forever but is a reasonable
Stage-1 / single-VPS choice (architecture Section 2). A SQL UNION is the
natural later optimisation once real usage data justifies it.

Ordering inside a tier is a TOTAL order, so a cursor can never land in
an ambiguous spot:

    following tier:  (created_at, content-type rank, id), all descending
    backfill tier:   (is_featured, created_at, content-type rank, id),
                      all descending

Home Feed algorithm (get_home_feed)
------------------------------------
A cursor's `phase` is the tier the LAST item of the previous page came
from — not "was I in the middle of backfilling". That is enough to
resume correctly:

  - cursor is None, or phase == following: query the following tier
    first (resuming from the cursor if it is a following cursor, or
    from the top otherwise). If it returns fewer items than the page
    needs, the following tier is exhausted for this user right now, so
    the remainder of THIS SAME page is filled from the top of the
    backfill tier (after=None) — this is the one following -> backfill
    transition, and it can only happen once, on this page.
  - cursor phase == backfill: the following tier was already exhausted
    on an earlier page (that is the only way a backfill cursor exists),
    so it is not queried again; resume the backfill tier from the
    cursor.

The next cursor is taken from the LAST item actually returned: backfill
phase if any backfill items were served this page, following phase
otherwise. A page with fewer items than requested is the end of the
feed (both tiers were exhausted), and gets next_cursor=None.
"""

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from django.db.models import Q

from businesses.models import BusinessProfile
from content.models import Post, Reel
from social.models import Follow
from feed.cursor import (
    CONTENT_TYPE_POST,
    CONTENT_TYPE_RANK,
    CONTENT_TYPE_REEL,
    PHASE_BACKFILL,
    PHASE_FOLLOWING,
    FeedCursor,
    decode_cursor,
    encode_cursor,
)

MAX_PAGE_SIZE = 50

# (content_type label, model) pairs, in one place so every tier iterates
# the same sources. Each model is only ever used via `.published_objects`.
_CONTENT_SOURCES = (
    (CONTENT_TYPE_POST, Post),
    (CONTENT_TYPE_REEL, Reel),
)


@dataclass(frozen=True)
class FeedEntry:
    """One item of the feed: a published Post or Reel plus its tier data."""

    content_type: str
    obj: Any
    is_featured: bool = False

    @property
    def id(self) -> int:
        return self.obj.pk

    @property
    def created_at(self):
        return self.obj.created_at

    def to_cursor(self, phase: str) -> FeedCursor:
        """The cursor that resumes the feed right AFTER this entry."""
        return FeedCursor(
            phase=phase,
            created_at=self.created_at,
            content_type=self.content_type,
            object_id=self.id,
            is_featured=self.is_featured if phase != PHASE_FOLLOWING else None,
        )


def following_sort_key(entry: FeedEntry):
    """Total order of the following tier (sort with reverse=True)."""
    return (entry.created_at, CONTENT_TYPE_RANK[entry.content_type], entry.id)


def backfill_sort_key(entry: FeedEntry):
    """Total order of the backfill tier (sort with reverse=True)."""
    return (
        entry.is_featured,
        entry.created_at,
        CONTENT_TYPE_RANK[entry.content_type],
        entry.id,
    )


def _following_rows_after(content_type: str, cursor: FeedCursor) -> Q:
    """
    Rows of `content_type` that come strictly AFTER `cursor` in the
    descending (created_at, rank, id) order, i.e. whose key is smaller.

    Compare the row key (ts, rank, id) with the cursor key (c_ts, c_rank,
    c_id):
      - older created_at                        -> after
      - same created_at and lower rank          -> after (any id)
      - same created_at, same rank, lower id    -> after
      - same created_at and higher rank         -> NOT after (already served)
    """
    rank = CONTENT_TYPE_RANK[content_type]
    cursor_rank = CONTENT_TYPE_RANK[cursor.content_type]
    if rank < cursor_rank:
        return Q(created_at__lte=cursor.created_at)
    if rank > cursor_rank:
        return Q(created_at__lt=cursor.created_at)
    return Q(created_at__lt=cursor.created_at) | Q(
        created_at=cursor.created_at, id__lt=cursor.object_id
    )


def _backfill_rows_after(content_type: str, cursor: FeedCursor) -> Q:
    """
    Rows of `content_type` that come strictly AFTER `cursor` in the
    descending (is_featured, created_at, rank, id) order.

    `is_featured` is resolved per row via `business__is_featured`, unlike
    `rank`, which is fixed for the whole query (each query only ever
    touches one content type). A row is "after" the cursor when either:
      - its is_featured is False and the cursor's is True (any timestamp), or
      - its is_featured matches the cursor's, and it is "after" on the same
        (created_at, rank, id) total order the following tier already
        uses (reused as-is: that comparison never looks at is_featured).
    """
    same_featured_tiebreak = _following_rows_after(content_type, cursor)
    if cursor.is_featured:
        return Q(business__is_featured=False) | (
            Q(business__is_featured=True) & same_featured_tiebreak
        )
    return Q(business__is_featured=False) & same_featured_tiebreak


def fetch_following_tier(
    followed_business_ids: Iterable[int],
    *,
    after: Optional[FeedCursor],
    limit: int,
) -> list:
    """
    Published Posts + Reels of the followed businesses, newest first,
    resuming strictly after `after` (a following-phase cursor) or from
    the very top when `after` is None. Returns at most `limit` FeedEntry.
    """
    if after is not None and after.phase != PHASE_FOLLOWING:
        raise ValueError("The following tier can only resume a following cursor")
    business_ids = list(followed_business_ids)
    if limit <= 0 or not business_ids:
        return []

    entries = []
    for content_type, model in _CONTENT_SOURCES:
        queryset = model.published_objects.filter(business_id__in=business_ids)
        if after is not None:
            queryset = queryset.filter(_following_rows_after(content_type, after))
        for obj in queryset.order_by("-created_at", "-id")[:limit]:
            entries.append(FeedEntry(content_type=content_type, obj=obj))

    entries.sort(key=following_sort_key, reverse=True)
    return entries[:limit]


def fetch_backfill_tier(
    excluded_business_ids: Iterable[int],
    *,
    after: Optional[FeedCursor],
    limit: int,
) -> list:
    """
    Published Posts + Reels NOT from `excluded_business_ids`, ordered
    Featured-first then newest first, resuming strictly after `after`
    (a backfill-phase cursor) or from the very top when `after` is None.
    Returns at most `limit` FeedEntry. Kept independent of
    `get_home_feed()` so P-062 (Discover) can reuse it directly.
    """
    if after is not None and after.phase != PHASE_BACKFILL:
        raise ValueError("The backfill tier can only resume a backfill cursor")
    if limit <= 0:
        return []
    excluded_ids = list(excluded_business_ids)

    entries = []
    for content_type, model in _CONTENT_SOURCES:
        queryset = model.published_objects.exclude(business_id__in=excluded_ids)
        if after is not None:
            queryset = queryset.filter(_backfill_rows_after(content_type, after))
        queryset = queryset.select_related("business").order_by(
            "-business__is_featured", "-created_at", "-id"
        )
        for obj in queryset[:limit]:
            entries.append(
                FeedEntry(
                    content_type=content_type,
                    obj=obj,
                    is_featured=obj.business.is_featured,
                )
            )

    entries.sort(key=backfill_sort_key, reverse=True)
    return entries[:limit]


def get_home_feed(user, cursor: Optional[str], page_size: int = 20) -> dict:
    """
    The Home Feed: followed-businesses content first, backfilled with
    Featured-first general content when the followed tier does not fill
    the page. Returns {"items": [FeedEntry, ...], "next_cursor": str|None}.

    Raises InvalidCursorError (a ValueError) if `cursor` is malformed,
    and ValueError if `page_size` is out of range — both are caller
    input errors for the view to turn into a 400.
    """
    if not isinstance(page_size, int) or page_size <= 0 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

    decoded_cursor = decode_cursor(cursor) if cursor else None

    followed_business_ids = list(
        Follow.objects.filter(follower=user).values_list("business_id", flat=True)
    )
    own_business_id = (
        BusinessProfile.objects.filter(user=user).values_list("id", flat=True).first()
    )
    excluded_business_ids = set(followed_business_ids)
    if own_business_id is not None:
        excluded_business_ids.add(own_business_id)

    following_items = []
    backfill_items = []

    if decoded_cursor is None or decoded_cursor.phase == PHASE_FOLLOWING:
        following_items = fetch_following_tier(
            followed_business_ids, after=decoded_cursor, limit=page_size
        )
        remaining = page_size - len(following_items)
        if remaining > 0:
            backfill_items = fetch_backfill_tier(
                excluded_business_ids, after=None, limit=remaining
            )
    else:
        backfill_items = fetch_backfill_tier(
            excluded_business_ids, after=decoded_cursor, limit=page_size
        )

    items = following_items + backfill_items

    next_cursor = None
    if len(items) >= page_size:
        if backfill_items:
            next_cursor = encode_cursor(backfill_items[-1].to_cursor(PHASE_BACKFILL))
        elif following_items:
            next_cursor = encode_cursor(following_items[-1].to_cursor(PHASE_FOLLOWING))

    return {"items": items, "next_cursor": next_cursor}


def get_discover_feed(user, cursor: Optional[str], page_size: int = 20) -> dict:
    """
    Part P-062 — Discover feed: backfill-tier-only, no following tier.

    Reuses `fetch_backfill_tier()` directly — that function's own
    docstring says it was kept independent of `get_home_feed()`
    specifically so this part could do exactly that; no new ordering
    logic is written here. Excludes the same business_ids
    `get_home_feed()` excludes (followed businesses + the user's own
    business, if any): Discover's whole point is surfacing businesses
    the user does NOT already follow — content from followed businesses
    already has a home in the Home Feed's following tier, and showing
    it again here would defeat the "browse broadly, find something new"
    purpose the presentation deck gives this screen.

    A cursor this function issues is always phase == PHASE_BACKFILL
    (there is no following tier here to ever produce a PHASE_FOLLOWING
    cursor). A cursor of the other phase, or a malformed one, raises
    the same ValueError get_home_feed() raises for its own bad cursors
    — the view is the one place that turns it into a 400.
    """
    if not isinstance(page_size, int) or page_size <= 0 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

    decoded_cursor = decode_cursor(cursor) if cursor else None
    if decoded_cursor is not None and decoded_cursor.phase != PHASE_BACKFILL:
        raise ValueError("Discover feed cursor must be a backfill-phase cursor")

    followed_business_ids = list(
        Follow.objects.filter(follower=user).values_list("business_id", flat=True)
    )
    own_business_id = (
        BusinessProfile.objects.filter(user=user).values_list("id", flat=True).first()
    )
    excluded_business_ids = set(followed_business_ids)
    if own_business_id is not None:
        excluded_business_ids.add(own_business_id)

    items = fetch_backfill_tier(
        excluded_business_ids, after=decoded_cursor, limit=page_size
    )

    next_cursor = None
    if len(items) >= page_size:
        next_cursor = encode_cursor(items[-1].to_cursor(PHASE_BACKFILL))

    return {"items": items, "next_cursor": next_cursor}
