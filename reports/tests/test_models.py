import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction

from core.models import TimestampedModel
from reports.models import Report

User = get_user_model()


def make_user(name):
    return User.objects.create_user(
        username=f"{name}@example.com",
        email=f"{name}@example.com",
        password="StrongPass123!",
    )


def make_report(reporter, target, reason=Report.Reason.SPAM, **extra):
    # Any model works as a target at the model layer; the real content-type
    # whitelist is enforced by the API layer (later step).
    return Report.objects.create(
        reporter=reporter,
        content_type=ContentType.objects.get_for_model(User),
        object_id=target.pk,
        reason=reason,
        **extra,
    )


@pytest.mark.django_db
class TestReportModel:
    def test_is_timestamped_model(self):
        assert issubclass(Report, TimestampedModel)
        report = make_report(make_user("a"), make_user("t"))
        assert report.created_at is not None
        assert report.updated_at is not None

    def test_defaults(self):
        report = make_report(make_user("a"), make_user("t"))
        assert report.status == "pending"
        assert report.details == ""

    def test_reason_choices(self):
        assert [v for v, _ in Report.Reason.choices] == [
            "spam",
            "inappropriate",
            "misleading",
            "other",
        ]

    def test_status_choices(self):
        assert [v for v, _ in Report.Status.choices] == ["pending", "reviewed"]

    def test_content_object_resolves_to_target(self):
        target = make_user("t")
        report = make_report(make_user("a"), target)
        assert report.content_object == target

    def test_details_stored(self):
        report = make_report(
            make_user("a"),
            make_user("t"),
            reason=Report.Reason.OTHER,
            details="Impersonates another shop",
        )
        report.refresh_from_db()
        assert report.details == "Impersonates another shop"

    def test_same_reporter_same_target_rejected(self):
        reporter, target = make_user("a"), make_user("t")
        make_report(reporter, target)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                make_report(reporter, target, reason=Report.Reason.MISLEADING)
        assert Report.objects.count() == 1

    def test_different_reporters_same_target_allowed(self):
        target = make_user("t")
        make_report(make_user("a"), target)
        make_report(make_user("b"), target)
        assert Report.objects.count() == 2

    def test_same_reporter_different_targets_allowed(self):
        reporter = make_user("a")
        make_report(reporter, make_user("t1"))
        make_report(reporter, make_user("t2"))
        assert Report.objects.count() == 2

    def test_deleting_reporter_cascades(self):
        reporter = make_user("a")
        make_report(reporter, make_user("t"))
        reporter.delete()
        assert Report.objects.count() == 0
