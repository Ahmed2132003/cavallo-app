"""
BE-04: per-business storage usage.

Counted: Product.image, Post.image, Reel.video + Reel.thumbnail,
Story.media (any moderation status; expired stories are purged, see
stories/tasks.py). NOT counted: chat media and business avatars (D10).

Source of truth is the monetization.StoredFile ledger (size captured at
upload, so reading usage never touches object storage):

  get_used_bytes(business)        SUM of the ledger.
  recompute_used_bytes(business)  rebuild the ledger from the real rows.
  sync_instance_files(instance)   called by storage_signals on save.
  forget_instance_files(instance) ledger rows + files, row already gone.
  purge_instance_files(instance)  ledger rows + files + clear the fields
                                  of a row that stays in the DB.

D11: deleting content really deletes its files. Storage deletes run in
transaction.on_commit (a rolled-back transaction never loses a file) and
are skipped for any file name another ledger row still references.
"""

import logging

from django.apps import apps
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce

from monetization.models import StoredFile

logger = logging.getLogger(__name__)

TRACKED_FIELDS = {
    "products.Product": ("image",),
    "content.Post": ("image",),
    "content.Reel": ("video", "thumbnail"),
    "stories.Story": ("media",),
}


def _pk(business):
    return getattr(business, "pk", business)


def _file_name(instance, field):
    file = getattr(instance, field, None)
    return file.name if file else ""


def _stored_size(name):
    """Size of an already-stored file (one storage call). 0 if unreadable."""
    try:
        return int(default_storage.size(name))
    except Exception:
        logger.exception("storage_size_failed", extra={"file_name": name})
        return 0


def _schedule_file_deletion(names):
    names = {name for name in names if name}
    if not names:
        return

    def _run():
        for name in names:
            # Shared file: another slot still points at it, keep it.
            if StoredFile.objects.filter(file_name=name).exists():
                continue
            try:
                default_storage.delete(name)
            except Exception:
                logger.exception("storage_delete_failed", extra={"file_name": name})

    transaction.on_commit(_run)


def get_used_bytes(business):
    """Bytes currently used by ``business`` (a BusinessProfile or its pk)."""
    total = StoredFile.objects.filter(business_id=_pk(business)).aggregate(
        total=Coalesce(Sum("size_bytes"), Value(0))
    )["total"]
    return int(total)


def sync_instance_files(instance, pending_sizes=None):
    """
    Bring the ledger rows of ``instance`` in line with its file fields.
    A changed file name (replacement) updates the row and deletes the old
    file after commit; an emptied field removes its row and file.
    """
    label = instance._meta.label
    pending_sizes = pending_sizes or {}
    old_names = []
    with transaction.atomic():
        rows = {
            row.field: row
            for row in StoredFile.objects.filter(model=label, object_id=instance.pk)
        }
        for field in TRACKED_FIELDS[label]:
            name = _file_name(instance, field)
            row = rows.get(field)
            if not name:
                if row is not None:
                    old_names.append(row.file_name)
                    row.delete()
                continue
            if (
                row is not None
                and row.file_name == name
                and row.business_id == instance.business_id
            ):
                continue
            if row is not None and row.file_name != name:
                old_names.append(row.file_name)
            size = pending_sizes.get(field)
            if size is None:
                size = _stored_size(name)
            StoredFile.objects.update_or_create(
                model=label,
                object_id=instance.pk,
                field=field,
                defaults={
                    "business_id": instance.business_id,
                    "file_name": name,
                    "size_bytes": size,
                },
            )
    _schedule_file_deletion(old_names)


def forget_instance_files(instance, fields=None):
    """Remove ledger rows and delete files; does not touch the instance row."""
    label = instance._meta.label
    fields = fields or TRACKED_FIELDS[label]
    with transaction.atomic():
        rows = StoredFile.objects.filter(
            model=label, object_id=instance.pk, field__in=fields
        )
        names = set(rows.values_list("file_name", flat=True))
        names.update(_file_name(instance, field) for field in fields)
        rows.delete()
    _schedule_file_deletion(names)


def purge_instance_files(instance, fields=None):
    """
    forget_instance_files() for a row that STAYS in the database (soft
    delete, expired story): also blanks the file fields, via a queryset
    update so no signal or updated_at change is triggered.
    """
    label = instance._meta.label
    fields = list(fields or TRACKED_FIELDS[label])
    with transaction.atomic():
        forget_instance_files(instance, fields)
        type(instance)._base_manager.filter(pk=instance.pk).update(
            **{field: "" for field in fields}
        )
    for field in fields:
        setattr(instance, field, "")


def recompute_used_bytes(business, refresh_sizes=False, purge_deleted=False):
    """
    Rebuild the ledger of ``business`` from its real rows and return the
    corrected used bytes. Existing sizes are reused unless
    ``refresh_sizes`` (then every size is re-read from storage).
    ``purge_deleted`` also purges the files of rows that were soft-deleted
    before BE-04 existed. Ledger rows whose content no longer exists are
    dropped (their files are not touched).
    """
    business_id = _pk(business)
    if purge_deleted:
        for label in TRACKED_FIELDS:
            model = apps.get_model(label)
            for obj in model._base_manager.filter(
                business_id=business_id, is_deleted=True
            ).iterator(chunk_size=500):
                if any(_file_name(obj, f) for f in TRACKED_FIELDS[label]):
                    purge_instance_files(obj)

    with transaction.atomic():
        existing = {
            (row.model, row.object_id, row.field): row
            for row in StoredFile.objects.filter(business_id=business_id)
        }
        seen = set()
        for label, fields in TRACKED_FIELDS.items():
            model = apps.get_model(label)
            qs = model._base_manager.filter(business_id=business_id, is_deleted=False)
            for obj in qs.iterator(chunk_size=500):
                for field in fields:
                    name = _file_name(obj, field)
                    if not name:
                        continue
                    key = (label, obj.pk, field)
                    seen.add(key)
                    row = existing.get(key)
                    if row and row.file_name == name and not refresh_sizes:
                        continue
                    StoredFile.objects.update_or_create(
                        model=label,
                        object_id=obj.pk,
                        field=field,
                        defaults={
                            "business_id": business_id,
                            "file_name": name,
                            "size_bytes": _stored_size(name),
                        },
                    )
        stale = [row.pk for key, row in existing.items() if key not in seen]
        if stale:
            StoredFile.objects.filter(pk__in=stale).delete()
    return get_used_bytes(business_id)