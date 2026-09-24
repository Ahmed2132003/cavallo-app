"""Report model (Part P-057).

A Report is a customer-submitted flag against a piece of content. It is a
generic relation (content_type + object_id), like Like/Save/Comment/Share.

Report's own lifecycle (``status``) is about whether an Admin has looked at
it. It is deliberately independent of ``social.Comment.is_hidden``: the
auto-hide of a reported Comment is a separate mechanism (P-055) that this
app only *triggers* (P-057 view layer).

Idempotency: one reporter can report a given target at most once
(``unique_together``). Without it, a single user could reach the Comment
auto-hide threshold alone.
"""

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from core.models import TimestampedModel


class Report(TimestampedModel):
    class Reason(models.TextChoices):
        SPAM = "spam", "Spam"
        INAPPROPRIATE = "inappropriate", "Inappropriate"
        MISLEADING = "misleading", "Misleading"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        REVIEWED = "reviewed", "Reviewed"

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="submitted_reports",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    reason = models.CharField(max_length=20, choices=Reason.choices)
    # Optional free text. Mainly meant for reason="other", but allowed for
    # any reason to add context.
    details = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )

    class Meta:
        unique_together = (("reporter", "content_type", "object_id"),)
        indexes = [
            models.Index(
                fields=["content_type", "object_id"],
                name="reports_report_target_idx",
            ),
            models.Index(
                fields=["status", "created_at"],
                name="reports_report_status_idx",
            ),
        ]

    def __str__(self):
        return (
            f"Report #{self.pk} ({self.reason}) on "
            f"{self.content_type_id}:{self.object_id}"
        )
