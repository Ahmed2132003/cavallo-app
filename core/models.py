"""
Shared abstract base model mixins (Part P-011).

Architecture Section 9 mandates soft delete on every content model
instead of hard delete. These mixins are built once, here, so every
future content app (Post, Reel, Story, Product, Comment, ...) inherits
identical, tested behavior rather than each app reinventing it slightly
differently.

Usage in a future app::

    from core.models import TimestampedModel, SoftDeleteModel

    class Post(TimestampedModel, SoftDeleteModel):
        ...

Deliberate exception: append-only models that must never be soft-deleted
(e.g. ModerationLog, see P-060/061) should NOT inherit SoftDeleteModel —
call that out explicitly in the part that defines them.
"""

from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    """
    Abstract base adding created_at / updated_at timestamps.

    created_at is set once, on first save. updated_at is refreshed on
    every save().
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteManager(models.Manager):
    """
    Default manager for SoftDeleteModel subclasses.

    Excludes soft-deleted rows (is_deleted=True) from every queryset
    built off this manager. Use SoftDeleteModel.all_objects to reach
    soft-deleted rows (e.g. for admin cleanup or auditing).
    """

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class SoftDeleteModel(models.Model):
    """
    Abstract base implementing soft delete instead of hard delete.

    - `.objects` (SoftDeleteManager) excludes soft-deleted rows by default
      — this is what every normal query, view, and serializer should use.
    - `.all_objects` (plain models.Manager) includes everything, including
      soft-deleted rows — for admin/cleanup use only.
    - `.delete()` performs a soft delete: sets is_deleted=True and
      deleted_at=now(), then saves. It does NOT call the real
      Model.delete() and does NOT remove the row from the database.
    - `.hard_delete()` performs a genuine, irreversible deletion by
      calling the real Model.delete(). Reserved for deliberate admin
      cleanup — never call this from ordinary application code paths.
    """

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        # Deliberately a full save() rather than save(update_fields=...):
        # TimestampedModel.updated_at (auto_now=True) is only refreshed by
        # Django for fields listed in update_fields, so restricting the
        # field list here would silently break the "every save() bumps
        # updated_at" contract for any model combining both mixins.
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(using=using)

    def hard_delete(self, using=None, keep_parents=False):
        """Genuine, irreversible deletion. Admin/cleanup use only."""
        super().delete(using=using, keep_parents=keep_parents)
