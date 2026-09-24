"""Report submission logic (Part P-057)."""

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import F

from reports.models import Report
from social import services as social_services
from social.models import Comment


def submit_report(*, reporter, target, reason, details=""):
    """Record a report. Returns ``(report, created)``.

    Idempotent per (reporter, target): a second report by the same user
    against the same target returns the existing row with ``created=False``
    and has no other effect.

    When a NEW report targets a Comment, inside the same transaction:
      1. the Report row is inserted (above),
      2. ``Comment.reports_count`` is incremented atomically with an F()
         expression (never read-then-write),
      3. P-055's ``check_and_hide_if_threshold_exceeded()`` is called. The
         threshold logic is NOT reimplemented here; that service reads the
         persisted DB value, so the caller's instance need not be refreshed.

    If step 2 or 3 raises, the whole transaction rolls back, so a Report can
    never exist without its counter increment. For any other target type the
    report is only recorded (an Admin-attention signal, no content effect).
    """
    content_type = ContentType.objects.get_for_model(target)
    with transaction.atomic():
        report, created = Report.objects.get_or_create(
            reporter=reporter,
            content_type=content_type,
            object_id=target.pk,
            defaults={"reason": reason, "details": details},
        )
        if created and isinstance(target, Comment):
            Comment.objects.filter(pk=target.pk).update(
                reports_count=F("reports_count") + 1
            )
            social_services.check_and_hide_if_threshold_exceeded(target)
    return report, created
