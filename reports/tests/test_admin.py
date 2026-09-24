import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from reports.models import Report

User = get_user_model()


@pytest.fixture
def report(db):
    reporter = User.objects.create_user(
        username="admin.reporter@example.com",
        email="admin.reporter@example.com",
        password="StrongPass123!",
    )
    target = User.objects.create_user(
        username="admin.target@example.com",
        email="admin.target@example.com",
        password="StrongPass123!",
    )
    return Report.objects.create(
        reporter=reporter,
        content_type=ContentType.objects.get_for_model(User),
        object_id=target.pk,
        reason=Report.Reason.SPAM,
    )


@pytest.mark.django_db
class TestReportAdmin:
    def test_report_is_registered(self):
        assert admin.site.is_registered(Report)

    def test_add_is_forbidden(self, admin_client):
        response = admin_client.get(reverse("admin:reports_report_add"))
        assert response.status_code == 403

    def test_changelist_shows_report(self, admin_client, report):
        response = admin_client.get(reverse("admin:reports_report_changelist"))
        assert response.status_code == 200
        assert b"admin.reporter@example.com" in response.content

    def test_mark_reviewed_action(self, admin_client, report):
        response = admin_client.post(
            reverse("admin:reports_report_changelist"),
            {"action": "mark_reviewed", "_selected_action": [report.pk]},
            follow=True,
        )
        assert response.status_code == 200
        report.refresh_from_db()
        assert report.status == Report.Status.REVIEWED
