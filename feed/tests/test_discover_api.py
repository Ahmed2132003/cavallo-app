"""
Part P-062 -- Discover feed API tests (HTTP level).

Mirrors feed/tests/test_api.py's own conventions for the Home Feed.
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from feed.tests.helpers import make_business, make_customer, make_follow, make_post, ts

DISCOVER_FEED_URL = reverse("discover-feed")


def _ids(response_data):
    return [(item["content_type"], item["id"]) for item in response_data["items"]]


class TestDiscoverFeedAuth(APITestCase):
    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get(DISCOVER_FEED_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestDiscoverFeedExcludesFollowed(APITestCase):
    def test_followed_business_content_never_appears(self):
        customer = make_customer()
        followed = make_business("Followed")
        make_follow(customer, followed)
        make_post(followed, at=ts(1))
        other = make_business("Other")
        other_post = make_post(other, at=ts(2))

        self.client.force_authenticate(user=customer)
        response = self.client.get(DISCOVER_FEED_URL, {"page_size": 10})

        assert response.status_code == status.HTTP_200_OK
        assert _ids(response.data) == [("post", other_post.id)]


class TestDiscoverFeedPagination(APITestCase):
    def test_next_cursor_pages_through_without_duplicates(self):
        customer = make_customer()
        biz = make_business("Biz")
        posts = [make_post(biz, at=ts(minute)) for minute in range(1, 6)]

        self.client.force_authenticate(user=customer)
        page1 = self.client.get(DISCOVER_FEED_URL, {"page_size": 3})
        assert page1.status_code == status.HTTP_200_OK
        assert len(page1.data["items"]) == 3
        assert page1.data["next_cursor"] is not None

        page2 = self.client.get(
            DISCOVER_FEED_URL,
            {"page_size": 3, "cursor": page1.data["next_cursor"]},
        )
        assert page2.status_code == status.HTTP_200_OK

        seen_ids = _ids(page1.data) + _ids(page2.data)
        expected_ids = [("post", p.id) for p in reversed(posts)]
        assert seen_ids == expected_ids
        assert page2.data["next_cursor"] is None
