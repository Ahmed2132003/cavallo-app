"""
API tests for GET /api/v1/analytics/business/{id}/daily/ (Part P-084).
"""

from datetime import date

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from analytics.models import BusinessDailyStats
from businesses.services import create_business_profile

User = get_user_model()

METRIC_KEYS = {
    "date",
    "new_followers",
    "total_likes_received",
    "total_comments_received",
    "total_story_views",
}


def _make_business_user(email, business_name):
    user = User.objects.create_user(
        username=email, email=email, password="testpass123", account_type="business"
    )
    business = create_business_profile(
        user=user,
        business_name=business_name,
        business_type="trader",
        country="EG",
        city="Ismailia",
    )
    return user, business


def _make_customer(email):
    return User.objects.create_user(
        username=email, email=email, password="testpass123", account_type="customer"
    )


def _stats(business, day, followers=0, likes=0, comments=0, story_views=0):
    return BusinessDailyStats.objects.create(
        business=business,
        date=day,
        new_followers=followers,
        total_likes_received=likes,
        total_comments_received=comments,
        total_story_views=story_views,
    )


def _url(business):
    return f"/api/v1/analytics/business/{business.pk}/daily/"


class _AnalyticsApiBase(APITestCase):
    def setUp(self):
        self.user_a, self.biz_a = _make_business_user(
            "analytics-api-a@example.com", "Analytics API A"
        )
        self.user_b, self.biz_b = _make_business_user(
            "analytics-api-b@example.com", "Analytics API B"
        )


class TestOwnerAccess(_AnalyticsApiBase):
    def test_owner_gets_own_stats_with_exact_values(self):
        _stats(
            self.biz_a,
            date(2026, 1, 2),
            followers=3,
            likes=5,
            comments=2,
            story_views=1,
        )
        self.client.force_authenticate(self.user_a)

        response = self.client.get(_url(self.biz_a))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(set(body), {"next", "previous", "results"})
        self.assertEqual(
            body["results"],
            [
                {
                    "date": "2026-01-02",
                    "new_followers": 3,
                    "total_likes_received": 5,
                    "total_comments_received": 2,
                    "total_story_views": 1,
                }
            ],
        )

    def test_response_contains_only_tracked_metrics(self):
        _stats(self.biz_a, date(2026, 1, 2), followers=1)
        self.client.force_authenticate(self.user_a)

        response = self.client.get(_url(self.biz_a))

        self.assertEqual(set(response.json()["results"][0]), METRIC_KEYS)

    def test_newest_date_first(self):
        for day in (date(2026, 1, 1), date(2026, 1, 3), date(2026, 1, 2)):
            _stats(self.biz_a, day)
        self.client.force_authenticate(self.user_a)

        response = self.client.get(_url(self.biz_a))

        self.assertEqual(
            [row["date"] for row in response.json()["results"]],
            ["2026-01-03", "2026-01-02", "2026-01-01"],
        )

    def test_other_business_rows_are_not_included(self):
        _stats(self.biz_a, date(2026, 1, 1), followers=1)
        _stats(self.biz_b, date(2026, 1, 1), followers=99)
        self.client.force_authenticate(self.user_a)

        response = self.client.get(_url(self.biz_a))

        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["new_followers"], 1)

    def test_other_business_owner_gets_403(self):
        _stats(self.biz_a, date(2026, 1, 1), followers=1)
        self.client.force_authenticate(self.user_b)

        response = self.client.get(_url(self.biz_a))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json()["error"]["code"], "PERMISSION_DENIED")

    def test_403_response_does_not_leak_stats(self):
        _stats(self.biz_a, date(2026, 1, 1), followers=1)
        self.client.force_authenticate(self.user_b)

        body = self.client.get(_url(self.biz_a)).json()

        self.assertNotIn("results", body)
        self.assertIn("error", body)

    def test_customer_without_business_gets_403(self):
        customer = _make_customer("analytics-api-customer@example.com")
        self.client.force_authenticate(customer)

        response = self.client.get(_url(self.biz_a))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_gets_401(self):
        response = self.client.get(_url(self.biz_a))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_nonexistent_business_gets_404(self):
        self.client.force_authenticate(self.user_a)

        response = self.client.get("/api/v1/analytics/business/999999/daily/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")

    def test_soft_deleted_business_gets_404(self):
        self.client.force_authenticate(self.user_a)
        self.biz_a.delete()  # soft delete

        response = self.client.get(_url(self.biz_a))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_endpoint_is_read_only(self):
        self.client.force_authenticate(self.user_a)
        url = _url(self.biz_a)

        for method in (
            self.client.post,
            self.client.put,
            self.client.patch,
            self.client.delete,
        ):
            response = method(url)
            self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(BusinessDailyStats.objects.count(), 0)


class TestDateRange(_AnalyticsApiBase):
    def setUp(self):
        super().setUp()
        for day in (
            date(2026, 1, 1),
            date(2026, 1, 2),
            date(2026, 1, 3),
            date(2026, 1, 4),
        ):
            _stats(self.biz_a, day)
        self.client.force_authenticate(self.user_a)

    def _dates(self, query=""):
        response = self.client.get(_url(self.biz_a) + query)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [row["date"] for row in response.json()["results"]]

    def test_date_from_only(self):
        self.assertEqual(
            self._dates("?date_from=2026-01-03"), ["2026-01-04", "2026-01-03"]
        )

    def test_date_to_only(self):
        self.assertEqual(
            self._dates("?date_to=2026-01-02"), ["2026-01-02", "2026-01-01"]
        )

    def test_both_bounds_are_inclusive(self):
        self.assertEqual(
            self._dates("?date_from=2026-01-02&date_to=2026-01-03"),
            ["2026-01-03", "2026-01-02"],
        )

    def test_no_match_returns_empty_list(self):
        self.assertEqual(self._dates("?date_from=2030-01-01"), [])

    def test_invalid_date_format_returns_400(self):
        response = self.client.get(_url(self.biz_a) + "?date_from=not-a-date")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        error = response.json()["error"]
        self.assertEqual(error["code"], "VALIDATION_ERROR")
        self.assertIn("date_from", error["fields"])

    def test_date_from_after_date_to_returns_400(self):
        response = self.client.get(
            _url(self.biz_a) + "?date_from=2026-01-04&date_to=2026-01-01"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date_from", response.json()["error"]["fields"])

    def test_ownership_is_checked_before_query_validation(self):
        self.client.force_authenticate(self.user_b)

        response = self.client.get(_url(self.biz_a) + "?date_from=not-a-date")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TestPagination(_AnalyticsApiBase):
    def test_cursor_pagination_walks_all_rows_newest_first(self):
        for day in (date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)):
            _stats(self.biz_a, day)
        self.client.force_authenticate(self.user_a)

        first = self.client.get(_url(self.biz_a) + "?page_size=2").json()
        self.assertEqual(
            [row["date"] for row in first["results"]], ["2026-01-03", "2026-01-02"]
        )
        self.assertIsNone(first["previous"])
        self.assertIsNotNone(first["next"])

        second = self.client.get(first["next"]).json()
        self.assertEqual([row["date"] for row in second["results"]], ["2026-01-01"])
        self.assertIsNone(second["next"])