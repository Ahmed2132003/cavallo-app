import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from reports.models import Report
from reports.tests.helpers import client_for, make_target, make_user, submit
from reports.throttles import ReportRateThrottle
from reports.views import ReportCreateView

# Default documented limit (DEFAULT_REPORT_THROTTLE_RATE == "10/hour").
LIMIT = 10


@pytest.mark.django_db
class TestReportRateLimitOnEndpoint:
    def test_eleventh_report_within_the_hour_is_429(self, business):
        """Genuinely trigger the limit: 10 real reports pass, the 11th is 429."""
        client = client_for(make_user("flooder"))
        targets = [make_target("post", business) for _ in range(LIMIT + 1)]

        statuses = [submit(client, "post", t.pk).status_code for t in targets[:LIMIT]]
        assert statuses == [201] * LIMIT

        response = submit(client, "post", targets[LIMIT].pk)
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "THROTTLED"
        assert Report.objects.count() == LIMIT

    def test_duplicate_reports_also_consume_the_quota(self, business):
        client = client_for(make_user("flooder"))
        target = make_target("post", business)

        statuses = [submit(client, "post", target.pk).status_code for _ in range(LIMIT)]
        assert statuses == [201] + [200] * (LIMIT - 1)

        assert submit(client, "post", target.pk).status_code == 429
        assert Report.objects.count() == 1

    def test_invalid_requests_also_consume_the_quota(self):
        client = client_for(make_user("flooder"))

        statuses = [
            submit(client, "post", 1, reason="bogus").status_code for _ in range(LIMIT)
        ]
        assert statuses == [400] * LIMIT

        assert submit(client, "post", 1, reason="bogus").status_code == 429

    def test_limit_is_per_user(self, business):
        target = make_target("post", business)
        flooder = client_for(make_user("flooder"))
        other = client_for(make_user("other"))

        for _ in range(LIMIT):
            submit(flooder, "post", target.pk)
        assert submit(flooder, "post", target.pk).status_code == 429

        assert submit(other, "post", target.pk).status_code == 201

    def test_unauthenticated_requests_do_not_consume_any_quota(self, business):
        target = make_target("post", business)
        anonymous = APIClient()

        statuses = {submit(anonymous, "post", target.pk).status_code for _ in range(15)}
        assert statuses == {401}

        # A real user is unaffected by the anonymous traffic.
        assert (
            submit(client_for(make_user("real")), "post", target.pk).status_code == 201
        )

    def test_other_endpoints_are_not_throttled_by_the_report_scope(self, business):
        client = client_for(make_user("flooder"))
        target = make_target("post", business)

        for _ in range(LIMIT):
            submit(client, "post", target.pk)
        assert submit(client, "post", target.pk).status_code == 429

        share = client.post(
            reverse("shares:create"),
            {"content_type": "post", "object_id": target.pk},
            format="json",
        )
        assert share.status_code == 201

    def test_limit_is_tunable_via_settings(self, business, settings):
        settings.REPORT_THROTTLE_RATE = "2/hour"
        client = client_for(make_user("flooder"))
        targets = [make_target("post", business) for _ in range(3)]

        statuses = [submit(client, "post", t.pk).status_code for t in targets]
        assert statuses == [201, 201, 429]


class TestReportViewThrottleWiring:
    def test_view_uses_only_the_dedicated_report_throttle(self):
        assert ReportCreateView.throttle_classes == [ReportRateThrottle]
