"""
BE-04: keep the StoredFile ledger in sync with Product / Post / Reel / Story.

- pre_save : capture the size of a NOT-yet-committed upload (free, no S3 call).
- post_save: sync the ledger; a soft delete (is_deleted=True) purges the files.
- post_delete: real / bulk deletes (queryset.delete() sends one per object).

Bypassed by queryset.update(): recompute_storage_usage repairs that drift.
"""

from django.db.models.signals import post_delete, post_save, pre_save

from monetization.storage_usage import (
    TRACKED_FIELDS,
    forget_instance_files,
    purge_instance_files,
    sync_instance_files,
)


def _capture_pending_sizes(sender, instance, raw=False, **kwargs):
    if raw:
        return
    pending = {}
    for field in TRACKED_FIELDS[sender._meta.label]:
        file = getattr(instance, field, None)
        if file and not getattr(file, "_committed", True):
            try:
                pending[field] = file.size
            except Exception:
                pass
    instance._storage_pending_sizes = pending


def _on_saved(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw:
        return
    fields = set(TRACKED_FIELDS[sender._meta.label]) | {"is_deleted"}
    if update_fields is not None and not (set(update_fields) & fields):
        return
    if getattr(instance, "is_deleted", False):
        purge_instance_files(instance)
        return
    sync_instance_files(instance, getattr(instance, "_storage_pending_sizes", None))
    instance._storage_pending_sizes = {}


def _on_deleted(sender, instance, **kwargs):
    forget_instance_files(instance)


for _label in TRACKED_FIELDS:
    pre_save.connect(
        _capture_pending_sizes,
        sender=_label,
        dispatch_uid=f"storage_pending_{_label}",
    )
    post_save.connect(
        _on_saved, sender=_label, dispatch_uid=f"storage_saved_{_label}"
    )
    post_delete.connect(
        _on_deleted, sender=_label, dispatch_uid=f"storage_deleted_{_label}"
    )