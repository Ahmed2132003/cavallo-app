"""
Part P-059 — Home Feed query service.

This module is built up across several steps of P-059. It currently
holds the shared building blocks and the FOLLOWING tier; the BACKFILL
tier and get_home_feed() are added in the following steps.

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
"""

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from django.db.models import Q

from content.models import Post, Reel
from feed.cursor import (
    CONTENT_TYPE_POST,
    CONTENT_TYPE_RANK,
    CONTENT_TYPE_REEL,
    PHASE_FOLLOWING,
    FeedCursor,
)

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