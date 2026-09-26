"""
Part P-062 -- get_discover_feed() service-level tests.

Follows test_get_home_feed.py's exact conventions (django.test.TestCase,
helpers, ts()). Covers: followed businesses are excluded, the caller's
own business is excluded, Featured-first ordering carries over
unchanged from fetch_backfill_tier(), and a non-backfill-phase cursor
is rejected.
"""

from django.test import TestCase

from feed.cursor import PHASE_FOLLOWING, encode_cursor
from feed.services import FeedEntry, get_discover_feed
from feed.tests.helpers import (
    make_business,
    make_customer,
    make_follow,
    make_post,
    ts,
)


def _keys(items):
    return [(entry.content_type, entry.id) for entry in items]


class TestDiscoverFeedExcludesFollowed(TestCase):
    def test_followed_business_content_is_excluded(self):
        customer = make_customer()
        followed = make_business("Followed")
        make_follow(customer, followed)
        make_post(followed, at=ts(1))
        other = make_business("Other")
        other_post = make_post(other, at=ts(2))

        result = get_discover_feed(customer, cursor=None, page_size=10)

        assert _keys(result["items"]) == [("post", other_post.id)]


class TestDiscoverFeedExcludesOwnBusiness(TestCase):
    def test_own_business_content_is_excluded(self):
        owner = make_business("Owner Biz")
        make_post(owner, at=ts(1))
        other = make_business("Other")
        other_post = make_post(other, at=ts(2))

        result = get_discover_feed(owner.user, cursor=None, page_size=10)

        assert _keys(result["items"]) == [("post", other_post.id)]


class TestDiscoverFeedFeaturedFirst(TestCase):
    def test_featured_business_content_ranks_first(self):
        customer = make_customer()
        regular = make_business("Regular")
        regular_post = make_post(regular, at=ts(50))
        featured = make_business("Featured", featured=True)
        featured_post = make_post(featured, at=ts(1))

        result = get_discover_feed(customer, cursor=None, page_size=10)

        assert _keys(result["items"]) == [
            ("post", featured_post.id),
            ("post", regular_post.id),
        ]


class TestDiscoverFeedCursorPhase(TestCase):
    def test_a_following_phase_cursor_is_rejected(self):
        customer = make_customer()
        biz = make_business("Biz")
        post = make_post(biz, at=ts(1))
        bad_cursor = encode_cursor(
            FeedEntry(content_type="post", obj=post).to_cursor(PHASE_FOLLOWING)
        )

        with self.assertRaises(ValueError):
            get_discover_feed(customer, cursor=bad_cursor, page_size=10)
