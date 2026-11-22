"""
BE-05: storage quota enforcement (check only; usage accounting is BE-04).

  ensure_quota_available(business, incoming_bytes)
      Raises StorageQuotaExceeded (403, STORAGE_QUOTA_EXCEEDED) when
      used + incoming > Subscription.quota_bytes. incoming_bytes <= 0
      (a smaller replacement) is always allowed. A business with NO
      Subscription row (legacy) has no quota and is never blocked.

  net_incoming_bytes(business, label, instance, files)
      New file size minus the size already stored in that slot (a
      replacement only counts the difference).

  quota_guard(business, serializer)
      Context manager used by the upload views. Runs the check BEFORE
      serializer.save() writes any file, so a refused request stores
      nothing (no partial save, no orphan file). The check and the save
      run in one transaction under a lock on the business row (the same
      lock activate_subscription takes), so two parallel uploads cannot
      both pass. Requests without a new file skip the lock entirely.

Deletes never call any of this: they are always allowed.
"""

from contextlib import contextmanager

from django.db import transaction
from django.utils.translation import gettext as _

from businesses.models import BusinessProfile
from core.exceptions import StorageQuotaExceeded
from monetization.models import StoredFile
from monetization.services import get_subscription
from monetization.storage_usage import TRACKED_FIELDS, get_used_bytes


def ensure_quota_available(business, incoming_bytes):
    if incoming_bytes <= 0:
        return
    subscription = get_subscription(business)
    if subscription is None:
        return
    used = get_used_bytes(business)
    quota = subscription.quota_bytes
    if used + incoming_bytes <= quota:
        return
    raise StorageQuotaExceeded(
        detail=_("Your storage is full. Upgrade your plan to upload more."),
        details={
            "used_bytes": used,
            "quota_bytes": quota,
            "incoming_bytes": incoming_bytes,
            "upgrade_required": True,
        },
    )


def net_incoming_bytes(business, label, instance, files):
    total = 0
    for field, file in files.items():
        old = 0
        if instance is not None:
            old = (
                StoredFile.objects.filter(
                    model=label, object_id=instance.pk, field=field
                )
                .values_list("size_bytes", flat=True)
                .first()
                or 0
            )
        total += file.size - old
    return total


@contextmanager
def quota_guard(business, serializer):
    """Use as: ``with quota_guard(business, serializer): serializer.save(...)``."""
    label = serializer.Meta.model._meta.label
    data = serializer.validated_data
    files = {
        field: data[field] for field in TRACKED_FIELDS.get(label, ()) if data.get(field)
    }
    if not files:
        yield
        return

    with transaction.atomic():
        BusinessProfile.all_objects.select_for_update().get(pk=business.pk)
        incoming = net_incoming_bytes(business, label, serializer.instance, files)
        ensure_quota_available(business, incoming)
        yield