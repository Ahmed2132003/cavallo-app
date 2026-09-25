"""
Part P-059 — backfill tier tests (service level, no HTTP).

The tier must: exclude followed/own businesses, order Featured-first
then newest-first with a stable tie-break, and resume from a cursor
with no duplicate and no missing item — including when many items
share both is_featured and created_at.
"""

from datetime import timedelta

from django.test import TestCase

from content.models import Reel
from feed.cursor import (
    PHASE_BACKFILL,
    PHASE_FOLLOWING,
    FeedCursor,
    decode_cursor,
    encode_cursor,
)
from feed.services import fetch_backfill_tier
from feed.tests.helpers import make_business, make_post, make_reel, ts


def _keys(entries):
    return [(entry.content_type, entry.id) for entry in entries]


def _expected_order(pairs):
    """pairs: list of (obj, is_featured). Independent reference ordering:
    Featured first, then newest first, Reel before Post on a tie, then
    higher id first."""

    def key(pair):
        obj, featured = pair
        return (featured, obj.created_at, 1 if isinstance(obj, Reel) else 0, obj.pk)

    ordered = sorted(pairs, key=key, reverse=True)
    return [("reel" if isinstance(o, Reel) else "post", o.pk) for o, _ in ordered]


class TestBackfillTierContent(TestCase):
    def setUp(self):
        self.followed = make_business("Followed")
        self.own = make_business("My Own Business")
        self.stranger_a = make_business("Stranger A")
        self.stranger_b = make_business("Stranger B", featured=True)

    def test_excluded_businesses_content_never_appears(self):
        make_post(self.followed, at=ts(1))
        make_post(self.own, at=ts(2))
        visible = make_post(self.stranger_a, at=ts(3))

        entries = fetch_backfill_tier(
            [self.followed.id, self.own.id], after=None, limit=10
        )

        assert _keys(entries) == [("post", visible.id)]

    def test_featured_content_ranks_above_non_featured_regardless_of_age(self):
        newer_regular = make_post(self.stranger_a, at=ts(10))
        older_featured = make_post(self.stranger_b, at=ts(1))

        entries = fetch_backfill_tier([], after=None, limit=10)

        assert _keys(entries) == [
            ("post", older_featured.id),
            ("post", newer_regular.id),
        ]

    def test_unpublished_content_never_appears(self):
        good = make_post(self.stranger_a, at=ts(1))
        make_post(self.stranger_a, at=ts(2), status="pending_review")
        make_post(self.stranger_a, at=ts(3), status="rejected")
        gone = make_post(self.stranger_a, at=ts(4))
        gone.delete()  # soft delete

        entries = fetch_backfill_tier([], after=None, limit=10)

        assert _keys(entries) == [("post", good.id)]


class TestBackfillTierOrderingAndPaging(TestCase):
    def setUp(self):
        self.biz_regular = make_business("Regular")
        self.biz_featured = make_business("Featured", featured=True)

    def _build_dataset_with_many_ties(self):
        pairs = []
        for minute in (1, 2, 3):
            for biz, featured in (
                (self.biz_regular, False),
                (self.biz_featured, True),
            ):
                pairs.append((make_post(biz, at=ts(minute)), featured))
                pairs.append((make_reel(biz, at=ts(minute)), featured))
        extra_time = ts(2) + timedelta(microseconds=7)
        pairs.append((make_post(self.biz_featured, at=extra_time), True))
        return pairs

    def test_paging_with_a_cursor_has_no_duplicates_and_no_gaps(self):
        expected = _expected_order(self._build_dataset_with_many_ties())

        for page_size in (1, 2, 3, 5, 100):
            collected = []
            cursor = None
            for _ in range(len(expected) + 2):
                page = fetch_backfill_tier([], after=cursor, limit=page_size)
                if not page:
                    break
                collected.extend(_keys(page))
                cursor = decode_cursor(
                    encode_cursor(page[-1].to_cursor(PHASE_BACKFILL))
                )
            assert collected == expected, f"page_size={page_size}"

    def test_cursor_after_the_last_item_returns_nothing(self):
        make_post(self.biz_regular, at=ts(1))
        last = fetch_backfill_tier([], after=None, limit=10)[-1]
        cursor = last.to_cursor(PHASE_BACKFILL)

        assert fetch_backfill_tier([], after=cursor, limit=10) == []

    def test_following_cursor_is_rejected(self):
        post = make_post(self.biz_regular, at=ts(1))
        following_cursor = FeedCursor(PHASE_FOLLOWING, post.created_at, "post", post.id)

        with self.assertRaises(ValueError):
            fetch_backfill_tier([], after=following_cursor, limit=10)


class TestBackfillTierEdgeCases(TestCase):
    def setUp(self):
        self.biz = make_business("Biz")
        make_post(self.biz, at=ts(1))

    def test_non_positive_limit_returns_empty_without_querying(self):
        with self.assertNumQueries(0):
            assert fetch_backfill_tier([], after=None, limit=0) == []
            assert fetch_backfill_tier([], after=None, limit=-1) == []

    def test_runs_exactly_one_query_per_content_type(self):
        with self.assertNumQueries(2):
            fetch_backfill_tier([], after=None, limit=10)

    def test_empty_exclusion_list_excludes_nothing(self):
        entries = fetch_backfill_tier([], after=None, limit=10)
        assert len(entries) == 1
