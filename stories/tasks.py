"""
Story expiry sweep — Celery Beat job (Part P-048).

Architecture Section 9 is explicit: Story visibility must be
QUERY-DRIVEN (expires_at > now(), evaluated fresh on every read), not
job-driven. This task is bookkeeping/analytics ONLY — it marks rows
that have already expired (per expires_at) with `archived_at`, purely
for cleanup/analytics/admin convenience. It is NEVER the mechanism
that makes a Story invisible; see stories/views.py's
StoryPublicListView (Part P-048, added alongside this task), whose
queryset condition (status="published" AND expires_at__gt=now()) is
the real, live visibility gate and does not depend on this task having
run.

Idempotent by construction: a single bulk .update() that only ever
touches rows where archived_at is still null. Running it twice in a
row updates zero additional rows the second time — no per-row Python
loop, no "already processed" branch needed beyond the filter itself.

Deliberately does NOT touch `status` or `is_deleted`. An expired-but-
still-"published" Story is simply invisible via the public endpoint's
query condition — it does not need to be "rejected" or soft-deleted.
Conflating "naturally expired" with "removed by a moderation/admin
decision" would corrupt what those two fields actually mean elsewhere
in the system (status is exclusively managed by
moderation.services.approve()/reject(); is_deleted is exclusively
managed by SoftDeleteModel.delete()). This task must never call either.
"""

from celery import shared_task
from django.utils import timezone

from stories.models import Story


@shared_task(name="stories.expire_stale_stories", ignore_result=True)
def expire_stale_stories():
    """
    Bulk-mark already-expired Stories as archived (bookkeeping only).

    A single idempotent bulk .update() call:
    Story.objects.filter(expires_at__lte=now, archived_at__isnull=True)
        .update(archived_at=now)

    Only rows where expires_at has already passed AND archived_at is
    still unset are touched; already-archived rows are excluded by the
    filter itself, so re-running this task immediately after is always
    a no-op (0 rows updated) for the same data. Uses the default
    Story.objects manager (SoftDeleteManager) — a soft-deleted Story is
    already excluded from every normal query and does not need
    archiving by this job.

    Returns a small summary dict — useful for
    `celery -A config call stories.expire_stale_stories` manual
    invocation output and for tests — but this is the only "state" this
    task exposes; see module docstring on why status/is_deleted are
    never touched here.
    """
    now = timezone.now()
    archived_count = Story.objects.filter(
        expires_at__lte=now,
        archived_at__isnull=True,
    ).update(archived_at=now)

    return {"archived_count": archived_count}
