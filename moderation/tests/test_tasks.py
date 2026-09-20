"""
Tests for moderation.tasks.check_moderation_sla (Part P-039).

Runs the task as a plain function call (Celery's task decorator makes
it directly callable without a worker) rather than through a real
Celery worker — matching the part's own testing instructions. Uses
QuerySet.update() to backdate created_at, since ModerationQueue
inherits TimestampedModel's auto_now_add=True, which would otherwise
silently overwrite any created_at passed to .create()/.save().
"""

import logging

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from datetime import timedelta

from moderation.models import ModerationQueue
from moderation.tasks import (
    FAST_PATH_SLA_MINUTES,
    NORMAL_SLA_HOURS,
    check_moderation_sla,
)
from moderation.tests.testapp.models import DummyContent


def _backdated_queue_item(*, priority, age):
    """
    Create a ModerationQueue row pointing at a real DummyContent
    instance, then backdate created_at past auto_now_add via
    QuerySet.update() (bypasses save()).
    """
    content = DummyContent.objects.create(title="dummy")
    item = ModerationQueue.objects.create(
        content_type=ContentType.objects.get_for_model(DummyContent),
        object_id=content.pk,
        status=ModerationQueue.Status.PENDING,
        priority=priority,
    )
    ModerationQueue.objects.filter(pk=item.pk).update(
        created_at=timezone.now() - age
    )
    item.refresh_from_db()
    return item


@pytest.mark.django_db
class TestCheckModerationSlaBreachDetection:
    def test_fast_path_item_past_threshold_is_logged_as_urgent(self, caplog):
        item = _backdated_queue_item(
            priority=ModerationQueue.Priority.FAST_PATH,
            age=timedelta(minutes=FAST_PATH_SLA_MINUTES + 1),
        )

        with caplog.at_level(logging.WARNING, logger="moderation.tasks"):
            result = check_moderation_sla()

        assert result["fast_path_breaches"] == 1
        breach_records = [
            r for r in caplog.records if r.message == "moderation_sla_breach"
        ]
        assert len(breach_records) == 1
        record = breach_records[0]
        assert record.levelname == "WARNING"
        assert record.severity == "urgent"
        assert record.queue_item_id == item.pk
        assert record.priority == ModerationQueue.Priority.FAST_PATH

    def test_normal_item_past_threshold_is_logged_as_warning(self, caplog):
        item = _backdated_queue_item(
            priority=ModerationQueue.Priority.NORMAL,
            age=timedelta(hours=NORMAL_SLA_HOURS + 1),
        )

        with caplog.at_level(logging.WARNING, logger="moderation.tasks"):
            result = check_moderation_sla()

        assert result["normal_breaches"] == 1
        breach_records = [
            r for r in caplog.records if r.message == "moderation_sla_breach"
        ]
        assert len(breach_records) == 1
        record = breach_records[0]
        assert record.severity == "warning"
        assert record.queue_item_id == item.pk
        assert record.priority == ModerationQueue.Priority.NORMAL

    def test_fast_path_item_under_threshold_is_not_logged(self, caplog):
        _backdated_queue_item(
            priority=ModerationQueue.Priority.FAST_PATH,
            age=timedelta(minutes=FAST_PATH_SLA_MINUTES - 5),
        )

        with caplog.at_level(logging.WARNING, logger="moderation.tasks"):
            result = check_moderation_sla()

        assert result["fast_path_breaches"] == 0
        assert not any(
            r.message == "moderation_sla_breach" for r in caplog.records
        )

    def test_approved_item_past_threshold_is_not_logged(self, caplog):
        """
        Only status=pending items count as breaches — an already
        reviewed item sitting in the queue is not a backlog problem.
        """
        content = DummyContent.objects.create(title="dummy")
        item = ModerationQueue.objects.create(
            content_type=ContentType.objects.get_for_model(DummyContent),
            object_id=content.pk,
            status=ModerationQueue.Status.APPROVED,
            priority=ModerationQueue.Priority.FAST_PATH,
        )
        ModerationQueue.objects.filter(pk=item.pk).update(
            created_at=timezone.now() - timedelta(minutes=FAST_PATH_SLA_MINUTES + 1)
        )

        with caplog.at_level(logging.WARNING, logger="moderation.tasks"):
            result = check_moderation_sla()

        assert result["fast_path_breaches"] == 0
        assert not any(
            r.message == "moderation_sla_breach" for r in caplog.records
        )


@pytest.mark.django_db
class TestCheckModerationSlaIdempotency:
    def test_running_twice_logs_the_same_still_breaching_item_both_times(
        self, caplog
    ):
        """
        Section 5 rule 8 / the part's explicit idempotency requirement:
        a still-breaching item must be logged again on every run, not
        suppressed after the first alert — the task keeps no
        "already alerted" state.
        """
        item = _backdated_queue_item(
            priority=ModerationQueue.Priority.FAST_PATH,
            age=timedelta(minutes=FAST_PATH_SLA_MINUTES + 1),
        )

        with caplog.at_level(logging.WARNING, logger="moderation.tasks"):
            first_result = check_moderation_sla()
            second_result = check_moderation_sla()

        assert first_result == second_result == {
            "fast_path_breaches": 1,
            "normal_breaches": 0,
        }

        breach_records = [
            r for r in caplog.records if r.message == "moderation_sla_breach"
        ]
        assert len(breach_records) == 2
        assert all(r.queue_item_id == item.pk for r in breach_records)

        # No mutation of the queue item itself — status/priority
        # untouched, confirming nothing was written as a side effect.
        item.refresh_from_db()
        assert item.status == ModerationQueue.Status.PENDING
        assert item.priority == ModerationQueue.Priority.FAST_PATH

    def test_running_with_no_breaches_produces_no_log_lines(self, caplog):
        with caplog.at_level(logging.WARNING, logger="moderation.tasks"):
            result = check_moderation_sla()
            result_again = check_moderation_sla()

        assert result == result_again == {
            "fast_path_breaches": 0,
            "normal_breaches": 0,
        }
        assert not any(
            r.message == "moderation_sla_breach" for r in caplog.records
        )