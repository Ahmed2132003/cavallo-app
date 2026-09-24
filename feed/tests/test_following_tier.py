"""
Part P-059 — following tier tests (service level, no HTTP).

The tier must: only read published content of followed businesses, order
Posts and Reels together newest-first with a stable tie-break, return an
EXACT top-N of the merged list, and resume from a cursor with no
duplicate and no missing item — including when many items share a
created_at.
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
from feed.services import fetch_following_tier
from feed.tests.helpers import make_business, make_post, make_reel, ts


def _keys(entries):
    return [(entry.content_type, entry.id) for entry in entries]


def _expected_order(objs):
    """Independent reference ordering: newest first, Reel before Post on a
    tie, then higher id first."""

    def key(obj):
        return (obj.created_at, 1 if isinstance(obj, Reel) else 0, obj.pk)

    ordered = sorted(objs, key=key, reverse=True)
    return [("reel" if isinstance(o, Reel) else "post", o.pk) for o in ordered]


class TestFollowingTierContent(TestCase):
    def setUp(self):
        self.biz_a = make_business("Biz A")
        self.biz_b = make_business("Biz B")
        self.stranger = make_business("Not Followed")

    def test_only_followed_businesses_content_is_returned(self):
        mine_a = make_post(self.biz_a, at=ts(1))
        mine_b = make_reel(self.biz_b, at=ts(2))
        make_post(self.stranger, at=ts(3))
        make_reel(self.stranger, at=ts(4))

        entries = fetch_following_tier(
            [self.biz_a.id, self.biz_b.id], after=None, limit=10
        )

        assert _keys(entries) == [("reel", mine_b.id), ("post", mine_a.id)]

    def test_posts_and_reels_are_merged_newest_first(self):
        p1 = make_post(self.biz_a, at=ts(1))
        r2 = make_reel(self.biz_a, at=ts(2))
        p3 = make_post(self.biz_b, at=ts(3))
        r4 = make_reel(self.biz_b, at=ts(4))
        p5 = make_post(self.biz_a, at=ts(5))

        entries = fetch_following_tier(
            [self.biz_a.id, self.biz_b.id], after=None, limit=10
        )

        assert _keys(entries) == [
            ("post", p5.id),
            ("reel", r4.id),
            ("post", p3.id),
            ("reel", r2.id),
            ("post", p1.id),
        ]

    def test_limit_returns_the_exact_top_of_the_merged_list(self):
        # The three newest items are all Reels: taking only `limit` rows
        # from each model must still produce the right merged top-2.
        for minute in (1, 2, 3):
            make_post(self.biz_a, at=ts(minute))
        r10 = make_reel(self.biz_a, at=ts(10))
        r9 = make_reel(self.biz_a, at=ts(9))
        make_reel(self.biz_a, at=ts(8))

        entries = fetch_following_tier([self.biz_a.id], after=None, limit=2)

        assert _keys(entries) == [("reel", r10.id), ("reel", r9.id)]

    def test_unpublished_content_never_appears(self):
        good_post = make_post(self.biz_a, at=ts(1))
        good_reel = make_reel(self.biz_a, at=ts(2))
        make_post(self.biz_a, at=ts(3), status="pending_review")
        make_post(self.biz_a, at=ts(4), status="rejected")
        gone_post = make_post(self.biz_a, at=ts(5))
        gone_post.delete()  # soft delete
        make_reel(self.biz_a, at=ts(6), status="pending_review")
        make_reel(self.biz_a, at=ts(7), status="rejected")
        make_reel(self.biz_a, at=ts(8), processing_status="processing")
        gone_reel = make_reel(self.biz_a, at=ts(9))
        gone_reel.delete()  # soft delete

        entries = fetch_following_tier([self.biz_a.id], after=None, limit=50)

        assert _keys(entries) == [("reel", good_reel.id), ("post", good_post.id)]


class TestFollowingTierOrderingAndPaging(TestCase):
    def setUp(self):
        self.biz_a = make_business("Biz A")
        self.biz_b = make_business("Biz B")
        self.ids = [self.biz_a.id, self.biz_b.id]

    def test_reel_comes_before_post_at_the_same_timestamp(self):
        post = make_post(self.biz_a, at=ts(5))
        reel = make_reel(self.biz_a, at=ts(5))

        entries = fetch_following_tier(self.ids, after=None, limit=10)

        assert _keys(entries) == [("reel", reel.id), ("post", post.id)]

    def test_same_type_same_timestamp_orders_by_id_descending(self):
        first = make_post(self.biz_a, at=ts(5))
        second = make_post(self.biz_b, at=ts(5))

        entries = fetch_following_tier(self.ids, after=None, limit=10)

        assert _keys(entries) == [("post", second.id), ("post", first.id)]

    def _build_dataset_with_many_ties(self):
        objs = []
        for minute in (1, 2, 3, 4, 5, 6):
            # Two Posts and two Reels on EVERY timestamp: worst case for ties.
            objs.append(make_post(self.biz_a, at=ts(minute)))
            objs.append(make_post(self.biz_b, at=ts(minute)))
            objs.append(make_reel(self.biz_a, at=ts(minute)))
            objs.append(make_reel(self.biz_b, at=ts(minute)))
        # Sub-second differences must be respected too.
        objs.append(make_post(self.biz_a, at=ts(3) + timedelta(microseconds=7)))
        objs.append(make_reel(self.biz_b, at=ts(3) + timedelta(microseconds=7)))
        return objs

    def test_paging_with_a_cursor_has_no_duplicates_and_no_gaps(self):
        expected = _expected_order(self._build_dataset_with_many_ties())

        for page_size in (1, 2, 3, 4, 5, 7, 100):
            collected = []
            cursor = None
            for _ in range(len(expected) + 2):  # hard stop against a loop bug
                page = fetch_following_tier(self.ids, after=cursor, limit=page_size)
                if not page:
                    break
                collected.extend(_keys(page))
                # Go through the real wire encoding on every hop.
                cursor = decode_cursor(
                    encode_cursor(page[-1].to_cursor(PHASE_FOLLOWING))
                )
            assert collected == expected, f"page_size={page_size}"

    def test_cursor_after_the_last_item_returns_nothing(self):
        make_post(self.biz_a, at=ts(1))
        make_reel(self.biz_a, at=ts(2))
        last = fetch_following_tier(self.ids, after=None, limit=10)[-1]

        assert last.content_type == "post"  # the oldest item is served last
        cursor = last.to_cursor(PHASE_FOLLOWING)
        assert fetch_following_tier(self.ids, after=cursor, limit=10) == []

    def test_backfill_cursor_is_rejected(self):
        post = make_post(self.biz_a, at=ts(1))
        backfill_cursor = FeedCursor(
            PHASE_BACKFILL, post.created_at, "post", post.id, False
        )

        with self.assertRaises(ValueError):
            fetch_following_tier(self.ids, after=backfill_cursor, limit=10)


class TestFollowingTierEdgeCases(TestCase):
    def setUp(self):
        self.biz = make_business("Biz")
        make_post(self.biz, at=ts(1))

    def test_no_followed_businesses_returns_empty_without_querying(self):
        with self.assertNumQueries(0):
            assert fetch_following_tier([], after=None, limit=10) == []

    def test_non_positive_limit_returns_empty_without_querying(self):
        with self.assertNumQueries(0):
            assert fetch_following_tier([self.biz.id], after=None, limit=0) == []
            assert fetch_following_tier([self.biz.id], after=None, limit=-3) == []

    def test_runs_exactly_one_query_per_content_type(self):
        with self.assertNumQueries(2):
            fetch_following_tier([self.biz.id], after=None, limit=10)

    def test_accepts_any_iterable_of_ids(self):
        assert len(fetch_following_tier({self.biz.id}, after=None, limit=10)) == 1
        assert (
            len(fetch_following_tier((i for i in [self.biz.id]), after=None, limit=10))
            == 1
        )