"""
Part P-059 — get_home_feed() tests: the following -> backfill merge,
the transition itself, and cross-page cursor correctness at the
service level (no HTTP; STEP 9 covers the API tests).
"""

from django.test import TestCase

from feed.cursor import InvalidCursorError
from feed.services import get_home_feed
from feed.tests.helpers import (
    make_business,
    make_customer,
    make_follow,
    make_post,
    ts,
)


def _keys(items):
    return [(entry.content_type, entry.id) for entry in items]


class TestGetHomeFeedFollowingOnly(TestCase):
    def test_followed_content_fills_the_page_without_touching_backfill(self):
        customer = make_customer()
        followed = make_business("Followed")
        make_follow(customer, followed)
        stranger = make_business("Stranger")
        make_post(stranger, at=ts(1))  # not followed -> eligible for backfill only
        p1 = make_post(followed, at=ts(2))
        p2 = make_post(followed, at=ts(3))

        # page_size exactly matches the followed tier's count, so
        # remaining == 0 and backfill is never queried.
        result = get_home_feed(customer, cursor=None, page_size=2)

        assert _keys(result["items"]) == [("post", p2.id), ("post", p1.id)]


class TestGetHomeFeedHybridFill(TestCase):
    def test_short_following_tier_is_backfilled_in_the_same_page(self):
        customer = make_customer()
        followed = make_business("Followed")
        make_follow(customer, followed)
        followed_post = make_post(followed, at=ts(100))
        backfill_biz = make_business("Backfill Source", featured=True)
        for minute in range(1, 5):
            make_post(backfill_biz, at=ts(minute))

        result = get_home_feed(customer, cursor=None, page_size=3)

        assert result["items"][0].content_type == "post"
        assert result["items"][0].id == followed_post.id
        assert len(result["items"]) == 3
        assert all(e.is_featured for e in result["items"][1:])

    def test_zero_follows_is_an_all_backfill_feed_from_page_one(self):
        customer = make_customer()
        biz = make_business("Some Business")
        p1 = make_post(biz, at=ts(1))
        p2 = make_post(biz, at=ts(2))

        result = get_home_feed(customer, cursor=None, page_size=10)

        assert _keys(result["items"]) == [("post", p2.id), ("post", p1.id)]


class TestGetHomeFeedPagination(TestCase):
    def test_no_duplicates_or_gaps_across_the_following_to_backfill_transition(self):
        customer = make_customer()
        followed = make_business("Followed")
        make_follow(customer, followed)
        followed_posts = [make_post(followed, at=ts(m)) for m in (10, 11, 12)]
        backfill_biz = make_business("Backfill")
        backfill_posts = [make_post(backfill_biz, at=ts(m)) for m in range(1, 8)]

        expected = [("post", p.id) for p in reversed(followed_posts)] + [
            ("post", p.id) for p in reversed(backfill_posts)
        ]

        # 50 is the service's MAX_PAGE_SIZE — stays within the allowed range.
        for page_size in (1, 2, 3, 4, 5, 10, 50):
            collected = []
            cursor = None
            for _ in range(len(expected) + 2):
                result = get_home_feed(customer, cursor=cursor, page_size=page_size)
                if not result["items"]:
                    break
                collected.extend(_keys(result["items"]))
                cursor = result["next_cursor"]
                if cursor is None:
                    break
            assert collected == expected, f"page_size={page_size}"

    def test_own_business_content_is_excluded_from_backfill(self):
        owner_business = make_business("My Business")
        owner = owner_business.user
        make_post(owner_business, at=ts(1))
        other = make_business("Other")
        visible = make_post(other, at=ts(2))

        result = get_home_feed(owner, cursor=None, page_size=10)

        assert _keys(result["items"]) == [("post", visible.id)]


class TestGetHomeFeedValidation(TestCase):
    def test_rejects_non_positive_page_size(self):
        customer = make_customer()
        with self.assertRaises(ValueError):
            get_home_feed(customer, cursor=None, page_size=0)

    def test_rejects_oversized_page_size(self):
        customer = make_customer()
        with self.assertRaises(ValueError):
            get_home_feed(customer, cursor=None, page_size=51)

    def test_rejects_malformed_cursor(self):
        customer = make_customer()
        with self.assertRaises(InvalidCursorError):
            get_home_feed(customer, cursor="not-a-real-cursor!!", page_size=10)
