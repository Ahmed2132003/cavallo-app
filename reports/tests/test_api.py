import pytest

from products.models import Product
from reports.models import Report
from reports.targets import REPORT_ALLOWED_CONTENT_TYPES
from reports.tests.helpers import client_for, make_target, make_user, submit

NON_COMMENT_KINDS = ["post", "reel", "story", "product", "business"]


@pytest.mark.django_db
class TestReportCreate:
    @pytest.mark.parametrize("kind", NON_COMMENT_KINDS)
    def test_report_on_non_comment_target_is_recorded_only(self, kind, business):
        target = make_target(kind, business)
        reporter = make_user("reporter")
        status_before = getattr(target, "status", None)

        response = submit(client_for(reporter), kind, target.pk)

        assert response.status_code == 201
        assert response.json() == {"reported": True}
        report = Report.objects.get()
        assert report.reporter == reporter
        assert report.content_object == target
        assert report.reason == "spam"
        assert report.status == Report.Status.PENDING
        # No side effect on the reported content itself.
        target.refresh_from_db()
        assert getattr(target, "status", None) == status_before

    def test_details_are_stored(self, business):
        target = make_target("post", business)
        response = submit(
            client_for(make_user("reporter")),
            "post",
            target.pk,
            reason="other",
            details="Sells counterfeit goods",
        )
        assert response.status_code == 201
        report = Report.objects.get()
        assert report.reason == "other"
        assert report.details == "Sells counterfeit goods"

    def test_duplicate_report_is_idempotent(self, business):
        target = make_target("post", business)
        client = client_for(make_user("reporter"))

        first = submit(client, "post", target.pk, reason="spam")
        second = submit(client, "post", target.pk, reason="misleading")

        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json() == {"reported": True}
        assert Report.objects.count() == 1
        assert Report.objects.get().reason == "spam"

    def test_different_users_each_create_a_report(self, business):
        target = make_target("post", business)
        for name in ("a", "b", "c"):
            response = submit(client_for(make_user(name)), "post", target.pk)
            assert response.status_code == 201
        assert Report.objects.count() == 3


@pytest.mark.django_db
class TestReportValidation:
    def test_unauthenticated_is_401_and_creates_nothing(self, client, business):
        from rest_framework.test import APIClient

        target = make_target("post", business)
        response = submit(APIClient(), "post", target.pk)
        assert response.status_code == 401
        assert Report.objects.count() == 0

    @pytest.mark.parametrize("missing", ["content_type", "object_id", "reason"])
    def test_missing_field_is_400(self, missing, business):
        target = make_target("post", business)
        payload = {"content_type": "post", "object_id": target.pk, "reason": "spam"}
        del payload[missing]
        from django.urls import reverse

        response = client_for(make_user("reporter")).post(
            reverse("reports:create"), payload, format="json"
        )
        assert response.status_code == 400
        assert Report.objects.count() == 0

    def test_invalid_reason_is_400(self, business):
        target = make_target("post", business)
        response = submit(
            client_for(make_user("reporter")), "post", target.pk, reason="hate"
        )
        assert response.status_code == 400
        assert Report.objects.count() == 0

    @pytest.mark.parametrize("bad_id", [0, "abc"])
    def test_invalid_object_id_is_400(self, bad_id):
        response = submit(client_for(make_user("reporter")), "post", bad_id)
        assert response.status_code == 400

    @pytest.mark.parametrize("kind", ["user", "chat", "nonsense"])
    def test_unsupported_content_type_is_400(self, kind, business):
        response = submit(client_for(make_user("reporter")), kind, 1)
        assert response.status_code == 400
        assert Report.objects.count() == 0

    def test_details_too_long_is_400(self, business):
        target = make_target("post", business)
        response = submit(
            client_for(make_user("reporter")),
            "post",
            target.pk,
            reason="other",
            details="x" * 1001,
        )
        assert response.status_code == 400
        assert Report.objects.count() == 0

    @pytest.mark.parametrize("kind", ["comment", *NON_COMMENT_KINDS])
    def test_nonexistent_target_is_404(self, kind):
        response = submit(client_for(make_user("reporter")), kind, 999999)
        assert response.status_code == 404
        assert Report.objects.count() == 0

    @pytest.mark.parametrize("kind", ["post", "reel", "story"])
    def test_unpublished_target_is_404(self, kind, business):
        target = make_target(kind, business, published=False)
        response = submit(client_for(make_user("reporter")), kind, target.pk)
        assert response.status_code == 404
        assert Report.objects.count() == 0

    def test_inactive_product_is_404(self, business):
        target = make_target("product", business)
        Product.objects.filter(pk=target.pk).update(is_active=False)
        response = submit(client_for(make_user("reporter")), "product", target.pk)
        assert response.status_code == 404
        assert Report.objects.count() == 0

    def test_get_is_405(self):
        from django.urls import reverse

        response = client_for(make_user("reporter")).get(reverse("reports:create"))
        assert response.status_code == 405


class TestReportWhitelist:
    def test_whitelist_is_exactly_the_scoped_types(self):
        assert set(REPORT_ALLOWED_CONTENT_TYPES) == {
            "comment",
            "post",
            "reel",
            "story",
            "product",
            "business",
        }
