"""
BE-09: permanent purge of an ended business's content. IRREVERSIBLE.

Safety layers:
  1. purge_blocker() is re-checked under a Subscription row lock.
  2. Every delete is pre-collected; if it would cascade into a model not
     in ALLOWED_DELETES, the whole business is rolled back.
  3. File names go to PendingFileDeletion in the same transaction and
     are removed only after the storage delete succeeds.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import F
from django.db.models.deletion import Collector
from django.utils import timezone

from monetization.constants import PURGE_GRACE_DAYS
from monetization.lifecycle import PAID_PURGE_GRACE_DAYS, drop_profile_cache
from monetization.models import PendingFileDeletion, StoredFile, Subscription
from monetization.notifications import notify_subscription_event
from monetization.storage_usage import TRACKED_FIELDS, get_used_bytes

logger = logging.getLogger(__name__)

ENDED_STATUSES = (Subscription.STATUS_LOCKED, Subscription.STATUS_EXPIRED)
INTERACTION_MODELS = ("social.Like", "social.Comment", "social.Save", "social.Share")
ALLOWED_DELETES = frozenset(
    {
        "products.product",
        "products.productvariant",
        "content.post",
        "content.reel",
        "stories.story",
        "stories.storyview",
        "social.like",
        "social.comment",
        "social.save",
        "social.share",
        "moderation.moderationqueue",
    }
)


class UnsafeCascade(Exception):
    pass


@dataclass
class PurgeReport:
    business_id: int
    dry_run: bool
    purged: bool = False
    skipped_reason: str = ""
    counts: dict = field(default_factory=dict)
    files: int = 0
    bytes: int = 0


def purge_blocker(sub, now):
    """None when the business may be purged, otherwise the reason."""
    if sub is None:
        return "no_subscription"
    if sub.status not in ENDED_STATUSES:
        return "not_ended"
    ends = [d for d in (sub.trial_ends_at, sub.current_period_end) if d]
    if not ends or sub.purge_at is None:
        return "missing_dates"
    last_end = max(ends)
    # Floor under purge_at: a wrong or stale value can never purge early.
    grace = PAID_PURGE_GRACE_DAYS if sub.current_period_end else PURGE_GRACE_DAYS
    if now < max(sub.purge_at, last_end + timedelta(days=grace)):
        return "not_due"
    if sub.purged_at is not None and sub.purged_at >= last_end:
        return "already_purged"
    return None


def _check_cascade(queryset):
    collector = Collector(using=queryset.db)
    collector.collect(queryset)
    touched = {model._meta.label_lower for model in collector.data}
    touched |= {
        qs.model._meta.label_lower for qs in collector.fast_deletes if qs.exists()
    }
    unexpected = touched - ALLOWED_DELETES
    if unexpected:
        raise UnsafeCascade(f"Delete would cascade into: {sorted(unexpected)}")


def _targets(business_id):
    """[(label, queryset)] in deletion order: dependants first."""
    content = []
    for label in TRACKED_FIELDS:
        model = apps.get_model(label)
        # _base_manager: includes soft-deleted rows and deletes for real.
        ids = list(
            model._base_manager.filter(business_id=business_id).values_list(
                "pk", flat=True
            )
        )
        content.append((label, model, ids))

    queue = apps.get_model("moderation", "ModerationQueue")
    queue_links = [
        f for f in queue._meta.private_fields if isinstance(f, GenericForeignKey)
    ]
    targets = []
    for _, model, ids in content:
        content_type = ContentType.objects.get_for_model(model)
        for label in INTERACTION_MODELS:
            qs = apps.get_model(label)._base_manager.filter(
                content_type=content_type, object_id__in=ids
            )
            targets.append((label, qs))
        # Otherwise moderators would see queue items with no content.
        for link in queue_links:
            qs = queue._base_manager.filter(
                **{link.ct_field: content_type, f"{link.fk_field}__in": ids}
            )
            targets.append(("moderation.ModerationQueue", qs))
    for label, model, ids in content:
        targets.append((label, model._base_manager.filter(pk__in=ids)))
    return targets


def _file_names(business_id):
    names = set(
        StoredFile.objects.filter(business_id=business_id).values_list(
            "file_name", flat=True
        )
    )
    for label, fields in TRACKED_FIELDS.items():
        rows = apps.get_model(label)._base_manager.filter(business_id=business_id)
        for values in rows.values_list(*fields):
            names.update(values)
    return {name for name in names if name}


def purge_business_content(business, dry_run=False, now=None):
    """
    Permanently delete the content of ``business`` (instance or pk) if the
    guard allows it. Returns a PurgeReport. dry_run writes nothing.
    """
    business_id = getattr(business, "pk", business)
    now = now or timezone.now()
    report = PurgeReport(business_id=business_id, dry_run=dry_run)

    with transaction.atomic():
        subs = Subscription.objects.filter(business_id=business_id)
        if not dry_run:
            # A concurrent renewal waits for this lock, or we see its result.
            subs = subs.select_for_update()
        sub = subs.first()
        reason = purge_blocker(sub, now)
        if reason:
            report.skipped_reason = reason
            return report

        targets = _targets(business_id)
        names = _file_names(business_id)
        report.files = len(names)
        report.bytes = get_used_bytes(business_id)
        for label, qs in targets:
            report.counts[label] = report.counts.get(label, 0) + qs.count()
            _check_cascade(qs)
        if dry_run:
            return report

        PendingFileDeletion.objects.bulk_create(
            [PendingFileDeletion(business_id=business_id, file_name=n) for n in names],
            ignore_conflicts=True,
        )
        for _, qs in targets:
            _check_cascade(qs)
            qs.delete()
        StoredFile.objects.filter(business_id=business_id).delete()

        sub.purged_at = now
        sub.is_featured_effective = False
        sub.save(update_fields=["purged_at", "is_featured_effective", "updated_at"])
        drop_profile_cache(business_id)
        transaction.on_commit(lambda: process_pending_file_deletions(business_id))
        # BE-11: tell the owner only once the purge has been committed.
        owner = sub.business
        transaction.on_commit(lambda: _notify_purge_done(owner))
        report.purged = True
    return report


def _notify_purge_done(business):
    try:
        notify_subscription_event(business, "purge_done")
    except Exception:  # never turn a committed purge into a failure
        logger.exception("purge_done notification failed business=%s", business.pk)


def process_pending_file_deletions(business_id=None):
    """Delete outbox files from storage. Failures stay for the next run."""
    pending = PendingFileDeletion.objects.all()
    if business_id is not None:
        pending = pending.filter(business_id=business_id)
    result = {"deleted": 0, "failed": 0, "kept": 0}
    for row in pending.order_by("attempts", "pk").iterator(chunk_size=500):
        try:
            if StoredFile.objects.filter(file_name=row.file_name).exists():
                result["kept"] += 1  # still referenced elsewhere
            else:
                default_storage.delete(row.file_name)
                result["deleted"] += 1
            row.delete()
        except Exception as exc:
            result["failed"] += 1
            logger.exception(
                "purge_file_delete_failed file=%s business=%s",
                row.file_name,
                row.business_id,
            )
            PendingFileDeletion.objects.filter(pk=row.pk).update(
                attempts=F("attempts") + 1,
                last_error=str(exc)[:500],
                updated_at=timezone.now(),
            )
    return result


def purge_due_businesses(dry_run=True, now=None):
    """Run the purge over all candidates, one transaction per business."""
    now = now or timezone.now()
    ids = list(
        Subscription.objects.filter(status__in=ENDED_STATUSES, purge_at__lte=now)
        .order_by("pk")
        .values_list("business_id", flat=True)
    )
    summary = {"purged": 0, "eligible": 0, "skipped": 0, "failed": 0, "reports": []}
    for business_id in ids:
        try:
            report = purge_business_content(business_id, dry_run=dry_run, now=now)
        except Exception:
            summary["failed"] += 1
            logger.exception("purge_failed business=%s", business_id)
            continue
        if report.skipped_reason:
            summary["skipped"] += 1
            continue
        summary["purged" if report.purged else "eligible"] += 1
        summary["reports"].append(report)
        logger.info(
            "purge_business business=%s dry_run=%s files=%s bytes=%s counts=%s",
            business_id,
            dry_run,
            report.files,
            report.bytes,
            report.counts,
        )
    return summary