"""
Moderation SLA alert Celery Beat job (Part P-039).

Architecture Section 17 names this the single most important
background job in the system: a slow-moderated Story (Phase 8) can
expire on its own 24h TTL before a human ever reviews it. This job's
only responsibility is DETECTION — flag any ModerationQueue item that
has sat in "pending" longer than its priority's threshold, by emitting
a structured (JSON, via P-015's logging config) log line at WARNING
level.

Out of scope (deliberately): real alerting/paging (Sentry/Slack/email).
That is Phase 21's Part P-105, which is expected to plug into this
job's log output. Do not add that wiring here.

Idempotency (Section 5 rule 8): this task performs NO writes and marks
nothing as "already alerted." Running it twice with the same breaching
item produces the same log line both times — repeated, persistent
visibility until the item is actually resolved (approved/rejected) is
the entire point, not a bug to "fix" by suppressing repeats.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from moderation.models import ModerationQueue

logger = logging.getLogger(__name__)

# --- Tunable thresholds -----------------------------------------------
# Placeholder values pending real-world tuning (P-039 spec). Change
# these two constants only — do not bury magic numbers in the queries
# below.
FAST_PATH_SLA_MINUTES = 30   # fast_path (Stories) pending longer than this = urgent
NORMAL_SLA_HOURS = 4         # normal priority pending longer than this = warning
# ------------------------------------------------------------------------


@shared_task(name="moderation.check_moderation_sla", ignore_result=True)
def check_moderation_sla():
    """
    Query ModerationQueue for SLA breaches and log each one at WARNING
    level with structured fields. Returns a small summary dict (useful
    for `celery -A config call ... ` manual invocation output and for
    tests) but writes no state — see module docstring on idempotency.
    """
    now = timezone.now()
    fast_path_cutoff = now - timedelta(minutes=FAST_PATH_SLA_MINUTES)
    normal_cutoff = now - timedelta(hours=NORMAL_SLA_HOURS)

    fast_path_breaches = ModerationQueue.objects.filter(
        status=ModerationQueue.Status.PENDING,
        priority=ModerationQueue.Priority.FAST_PATH,
        created_at__lt=fast_path_cutoff,
    )
    for item in fast_path_breaches:
        age_seconds = (now - item.created_at).total_seconds()
        logger.warning(
            "moderation_sla_breach",
            extra={
                "event": "moderation_sla_breach",
                "severity": "urgent",
                "queue_item_id": item.id,
                "content_type": str(item.content_type),
                "priority": item.priority,
                "age_seconds": round(age_seconds, 1),
                "threshold_minutes": FAST_PATH_SLA_MINUTES,
            },
        )

    normal_breaches = ModerationQueue.objects.filter(
        status=ModerationQueue.Status.PENDING,
        priority=ModerationQueue.Priority.NORMAL,
        created_at__lt=normal_cutoff,
    )
    for item in normal_breaches:
        age_seconds = (now - item.created_at).total_seconds()
        # WARNING for normal breaches too (documented choice — this
        # job's whole purpose is to make backlog impossible to ignore;
        # INFO-level breach lines are too easy to filter out in
        # production log levels).
        logger.warning(
            "moderation_sla_breach",
            extra={
                "event": "moderation_sla_breach",
                "severity": "warning",
                "queue_item_id": item.id,
                "content_type": str(item.content_type),
                "priority": item.priority,
                "age_seconds": round(age_seconds, 1),
                "threshold_hours": NORMAL_SLA_HOURS,
            },
        )

    return {
        "fast_path_breaches": fast_path_breaches.count(),
        "normal_breaches": normal_breaches.count(),
    }