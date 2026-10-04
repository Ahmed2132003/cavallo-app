"""
Moderation SLA alert Celery Beat job (Part P-039).

Architecture Section 17 names this the single most important
background job in the system: a slow-moderated Story (Phase 8) can
expire on its own 24h TTL before a human ever reviews it. This job's
only responsibility is DETECTION — flag any ModerationQueue item that
has sat in "pending" longer than its priority's threshold, by emitting
a structured (JSON, via P-015's logging config) log line at WARNING
level.

Alerting (Part P-105): after the per-item log lines, each run also sends
ONE aggregated Sentry event per breached tier (fast_path -> level "error",
normal -> level "warning"); see _alert_sla_breach() below. Sentry's own
alerting (email at minimum) is the notification channel; no Slack/SMS.

Idempotency (Section 5 rule 8): this task performs NO writes and marks
nothing as "already alerted." Running it twice with the same breaching
item produces the same log line both times — repeated, persistent
visibility until the item is actually resolved (approved/rejected) is
the entire point, not a bug to "fix" by suppressing repeats.
"""

import logging
from datetime import timedelta

import sentry_sdk
from celery import shared_task
from django.utils import timezone

from moderation.models import ModerationQueue

logger = logging.getLogger(__name__)

# --- Tunable thresholds -----------------------------------------------
# Placeholder values pending real-world tuning (P-039 spec). Change
# these two constants only — do not bury magic numbers in the queries
# below.
FAST_PATH_SLA_MINUTES = 30  # fast_path (Stories) pending longer than this = urgent
NORMAL_SLA_HOURS = 4  # normal priority pending longer than this = warning
# ------------------------------------------------------------------------


# Maximum number of queue item ids attached to one Sentry alert (keeps the
# event small when a large backlog builds up).
SENTRY_MAX_ITEM_IDS = 20


def _alert_sla_breach(*, breaches, now, priority, level, threshold_text):
    """
    Part P-105: send ONE aggregated Sentry event for this run's breaches of
    one priority tier (the real "someone gets notified" half of this job;
    the per-item WARNING log lines above stay exactly as they were).

    One event per tier per run (not one per item) so a large backlog cannot
    flood Sentry. The fingerprint is constant per tier, so every run's
    event for the same tier lands in the SAME Sentry issue; the live,
    changing numbers (count, oldest age) are in the message and context.

    A monitoring failure must never break this task, so any error here is
    swallowed after a log line.
    """
    items = list(breaches)  # already evaluated by the caller's loop: no new query
    if not items:
        return
    try:
        ages = [(now - item.created_at).total_seconds() for item in items]
        oldest_seconds = max(ages)
        if priority == ModerationQueue.Priority.FAST_PATH:
            message = (
                f"Moderation SLA breach: {len(items)} fast_path item(s) pending "
                f"over {threshold_text} (oldest {round(oldest_seconds / 60)} min)"
            )
        else:
            message = (
                f"Moderation SLA warning: {len(items)} normal item(s) pending "
                f"over {threshold_text} (oldest {oldest_seconds / 3600:.1f} h)"
            )
        with sentry_sdk.new_scope() as scope:
            scope.fingerprint = ["moderation-sla-breach", str(priority)]
            scope.set_tag("moderation_sla_priority", str(priority))
            scope.set_context(
                "moderation_sla",
                {
                    "breach_count": len(items),
                    "oldest_age_seconds": round(oldest_seconds, 1),
                    "threshold": threshold_text,
                    "queue_item_ids": [i.id for i in items[:SENTRY_MAX_ITEM_IDS]],
                },
            )
            sentry_sdk.capture_message(message, level=level)
    except Exception:  # monitoring must never break this task
        logger.warning("moderation_sla_alert_failed", exc_info=True)


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

    # Part P-105: real alerts (Sentry), after all log lines are written.
    # fast_path is the urgent tier and is sent first.
    _alert_sla_breach(
        breaches=fast_path_breaches,
        now=now,
        priority=ModerationQueue.Priority.FAST_PATH,
        level="error",
        threshold_text=f"{FAST_PATH_SLA_MINUTES} min",
    )
    _alert_sla_breach(
        breaches=normal_breaches,
        now=now,
        priority=ModerationQueue.Priority.NORMAL,
        level="warning",
        threshold_text=f"{NORMAL_SLA_HOURS} h",
    )

    return {
        "fast_path_breaches": fast_path_breaches.count(),
        "normal_breaches": normal_breaches.count(),
    }
