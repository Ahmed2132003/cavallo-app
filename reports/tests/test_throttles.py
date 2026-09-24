import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from reports.throttles import DEFAULT_REPORT_THROTTLE_RATE, ReportRateThrottle

User = get_user_model()
factory = APIRequestFactory()


@pytest.fixture(autouse=True)
def _clean_cache():
    # Throttle counters live in the real (Redis) cache; start and end clean so
    # tests never leak counters into each other (same precedent as P-040).
    cache.clear()
    yield
    cache.clear()


def make_user(name):
    return User.objects.create_user(
        username=f"{name}@example.com",
        email=f"{name}@example.com",
        password="StrongPass123!",
    )


def request_for(user):
    request = Request(factory.post("/api/v1/reports/"))
    request.user = user
    return request


def hit(user):
    """One simulated request: DRF builds a fresh throttle per request."""
    throttle = ReportRateThrottle()
    return throttle.allow_request(request_for(user), view=None), throttle


class TestReportThrottleConfig:
    def test_scope_is_dedicated(self):
        assert ReportRateThrottle.scope == "report"
        assert ReportRateThrottle.scope not in ("login", "user", "anon")

    def test_default_rate_is_ten_per_hour(self):
        throttle = ReportRateThrottle()
        assert DEFAULT_REPORT_THROTTLE_RATE == "10/hour"
        assert throttle.rate == "10/hour"
        assert throttle.num_requests == 10
        assert throttle.duration == 3600

    def test_rate_can_be_overridden_via_settings(self, settings):
        settings.REPORT_THROTTLE_RATE = "3/hour"
        throttle = ReportRateThrottle()
        assert throttle.num_requests == 3
        assert throttle.duration == 3600


@pytest.mark.django_db
class TestReportThrottleEnforcement:
    def test_blocks_after_limit(self, settings):
        settings.REPORT_THROTTLE_RATE = "3/hour"
        user = make_user("flooder")
        results = [hit(user)[0] for _ in range(3)]
        assert results == [True, True, True]
        allowed, throttle = hit(user)
        assert allowed is False
        assert throttle.wait() > 0

    def test_limit_is_per_user(self, settings):
        settings.REPORT_THROTTLE_RATE = "3/hour"
        flooder, other = make_user("flooder"), make_user("other")
        for _ in range(3):
            hit(flooder)
        assert hit(flooder)[0] is False
        assert hit(other)[0] is True

    def test_cache_key_uses_report_scope(self):
        user = make_user("keyed")
        throttle = ReportRateThrottle()
        key = throttle.get_cache_key(request_for(user), view=None)
        assert key == f"throttle_report_{user.pk}"
