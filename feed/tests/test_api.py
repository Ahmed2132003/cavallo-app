"""
Part P-059 — Home Feed API tests (HTTP level).

Covers the five cases the spec calls out explicitly, most importantly
the critical cross-page pagination test: no duplicate and no missing
item across the followed -> backfill transition when paging through
the real HTTP response's next_cursor.
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from feed.tests.helpers import make_business, make_customer, make_follow, make_post, ts

HOME_FEED_URL = reverse("home-feed")


def _ids(response_data):
    return [(item["content_type"], item["id"]) for item in response_data["items"]]


class TestHomeFeedAuth(APITestCase):
    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get(HOME_FEED_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestHomeFeedFollowingOnly(APITestCase):
    def test_followed_content_appears_first_and_ordered(self):
        customer = make_customer()
        biz_a = make_business("Biz A")
        biz_b = make_business("Biz B")
        make_follow(customer, biz_a)
        make_follow(customer, biz_b)
        p1 = make_post(biz_a, at=ts(1))
        p2 = make_post(biz_b, at=ts(2))

        self.client.force_authenticate(user=customer)
        response = self.client.get(HOME_FEED_URL, {"page_size": 2})

        assert response.status_code == status.HTTP_200_OK
        assert _ids(response.data) == [("post", p2.id), ("post", p1.id)]


class TestHomeFeedHybridFill(APITestCase):
    def test_short_followed_tier_is_backfilled_with_featured_first(self):
        customer = make_customer()
        followed = make_business("Followed")
        make_follow(customer, followed)
        followed_post = make_post(followed, at=ts(100))
        featured = make_business("Featured Backfill", featured=True)
        for minute in range(1, 20):
            make_post(featured, at=ts(minute))

        self.client.force_authenticate(user=customer)
        response = self.client.get(HOME_FEED_URL, {"page_size": 20})

        assert response.status_code == status.HTTP_200_OK
        items = response.data["items"]
        assert len(items) == 20
        assert items[0]["id"] == followed_post.id
        assert all(item["id"] != followed_post.id for item in items[1:])


class TestHomeFeedZeroFollows(APITestCase):
    def test_all_backfill_from_page_one(self):
        customer = make_customer()
        biz = make_business("Some Business")
        p1 = make_post(biz, at=ts(1))
        p2 = make_post(biz, at=ts(2))

        self.client.force_authenticate(user=customer)
        response = self.client.get(HOME_FEED_URL, {"page_size": 10})

        assert response.status_code == status.HTTP_200_OK
        assert _ids(response.data) == [("post", p2.id), ("post", p1.id)]


class TestHomeFeedUnpublishedContentNeverAppears(APITestCase):
    def test_pending_and_rejected_content_from_a_followed_business_is_hidden(self):
        customer = make_customer()
        followed = make_business("Followed")
        make_follow(customer, followed)
        good = make_post(followed, at=ts(1))
        make_post(followed, at=ts(2), status="pending_review")
        make_post(followed, at=ts(3), status="rejected")

        self.client.force_authenticate(user=customer)
        response = self.client.get(HOME_FEED_URL, {"page_size": 10})

        assert response.status_code == status.HTTP_200_OK
        assert _ids(response.data) == [("post", good.id)]


class TestHomeFeedCriticalPagination(APITestCase):
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

        self.client.force_authenticate(user=customer)

        for page_size in (1, 2, 3, 4, 5, 10):
            collected = []
            cursor = None
            for _ in range(len(expected) + 2):
                params = {"page_size": page_size}
                if cursor:
                    params["cursor"] = cursor
                response = self.client.get(HOME_FEED_URL, params)
                assert response.status_code == status.HTTP_200_OK
                if not response.data["items"]:
                    break
                collected.extend(_ids(response.data))
                cursor = response.data["next_cursor"]
                if cursor is None:
                    break
            assert collected == expected, f"page_size={page_size}"

    def test_malformed_cursor_returns_400(self):
        customer = make_customer()
        self.client.force_authenticate(user=customer)

        response = self.client.get(HOME_FEED_URL, {"cursor": "not-a-real-cursor!!"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_out_of_range_page_size_returns_400(self):
        customer = make_customer()
        self.client.force_authenticate(user=customer)

        response = self.client.get(HOME_FEED_URL, {"page_size": 999})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
