"""
Part P-060 — Redis caching of the Home Feed's first page (HTTP level).

Only the caching contract added by this Part is tested here: P-059's
own test_api.py already proves the underlying feed algorithm itself is
correct, so these tests never assert on feed *contents* beyond "the two
responses are identical" — they assert on how many times the expensive
feed_services.get_home_feed() computation actually ran, via
mock.patch(..., wraps=...) (a call-count assertion, per this Part's own
Testing note allowing either a query-count or a call-count check —
call-count was chosen because the number of DB queries the real feed
algorithm issues on a miss is P-059's own implementation detail, not
something this Part should re-pin).
"""

from unittest import mock

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

import feed.views as feed_views
from feed.tests.helpers import make_business, make_customer, make_follow, make_post, ts

HOME_FEED_URL = reverse("home-feed")


class TestHomeFeedCachingFirstPage(APITestCase):
    """
    Part P-060 acceptance criterion #1: repeated first-page (no
    ?cursor=) requests within the TTL window don't re-hit
    get_home_feed()'s underlying computation.
    """

    def setUp(self):
        # Clean cache DB per test — same reasoning as P-030's own
        # caching tests (businesses/tests/test_api.py) and P-014's
        # core/tests/test_cache.py: avoids cross-test leakage on the
        # shared Redis cache DB.
        cache.clear()

    def test_second_default_first_page_request_does_not_recompute(self):
        customer = make_customer()
        followed = make_business("Followed")
        make_follow(customer, followed)
        make_post(followed, at=ts(1))
        self.client.force_authenticate(user=customer)

        with mock.patch(
            "feed.views.get_home_feed", wraps=feed_views.get_home_feed
        ) as spy:
            first = self.client.get(HOME_FEED_URL)
            second = self.client.get(HOME_FEED_URL)

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        assert first.data == second.data
        assert spy.call_count == 1

    def test_different_users_get_independent_first_page_caches(self):
        customer_a = make_customer()
        customer_b = make_customer()
        followed = make_business("Followed")
        make_follow(customer_a, followed)
        make_follow(customer_b, followed)
        make_post(followed, at=ts(1))

        with mock.patch(
            "feed.views.get_home_feed", wraps=feed_views.get_home_feed
        ) as spy:
            self.client.force_authenticate(user=customer_a)
            self.client.get(HOME_FEED_URL)
            self.client.force_authenticate(user=customer_b)
            self.client.get(HOME_FEED_URL)

        # Two different users' page-1 requests must each be a genuine
        # miss under their own cache key (feed:{user_id}:page1) — not
        # accidentally served from each other's cached entry.
        assert spy.call_count == 2


class TestHomeFeedCachingBypass(APITestCase):
    """
    Part P-060 acceptance criterion #2: a cursor-bearing request always
    bypasses the cache and computes fresh. Also covers this
    implementation's documented deviation: a page-1 request with an
    explicit, non-default page_size also always bypasses the cache
    (see feed/views.py's _get_feed_page docstring).
    """

    def setUp(self):
        cache.clear()

    def test_cursor_request_is_never_served_from_cache(self):
        customer = make_customer()
        followed = make_business("Followed")
        make_follow(customer, followed)
        for minute in range(1, 4):
            make_post(followed, at=ts(minute))
        self.client.force_authenticate(user=customer)

        first = self.client.get(HOME_FEED_URL, {"page_size": 1})
        assert first.status_code == status.HTTP_200_OK
        cursor = first.data["next_cursor"]
        assert cursor is not None

        with mock.patch(
            "feed.views.get_home_feed", wraps=feed_views.get_home_feed
        ) as spy:
            second = self.client.get(HOME_FEED_URL, {"page_size": 1, "cursor": cursor})
            third = self.client.get(HOME_FEED_URL, {"page_size": 1, "cursor": cursor})

        assert second.status_code == status.HTTP_200_OK
        assert third.status_code == status.HTTP_200_OK
        # Same cursor requested twice still computes twice — cursor
        # requests are never cached at all, not even under their own key.
        assert spy.call_count == 2

    def test_explicit_non_default_page_size_on_page_one_is_never_cached(self):
        customer = make_customer()
        followed = make_business("Followed")
        make_follow(customer, followed)
        make_post(followed, at=ts(1))
        self.client.force_authenticate(user=customer)

        with mock.patch(
            "feed.views.get_home_feed", wraps=feed_views.get_home_feed
        ) as spy:
            first = self.client.get(HOME_FEED_URL, {"page_size": 5})
            second = self.client.get(HOME_FEED_URL, {"page_size": 5})

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        assert spy.call_count == 2

    def test_default_page_size_cache_does_not_leak_into_explicit_page_size_request(
        self,
    ):
        customer = make_customer()
        followed = make_business("Followed")
        make_follow(customer, followed)
        make_post(followed, at=ts(1))
        self.client.force_authenticate(user=customer)

        # Warm the default (page_size=20) cache entry first.
        warm = self.client.get(HOME_FEED_URL)
        assert warm.status_code == status.HTTP_200_OK

        with mock.patch(
            "feed.views.get_home_feed", wraps=feed_views.get_home_feed
        ) as spy:
            response = self.client.get(HOME_FEED_URL, {"page_size": 5})

        assert response.status_code == status.HTTP_200_OK
        # Must be a genuine miss under the explicit page_size, not
        # accidentally served the default-page_size cached entry.
        assert spy.call_count == 1
