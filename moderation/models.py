"""
Moderation app: generic ModerationQueue + the Moderatable mixin (Part P-036).

Architecture Sections 6/17/28 name moderation as the platform's central
risk subsystem: every future content type (Post, Reel, Story — Phases 7
and 8) MUST plug into this one shared mechanism rather than rolling its
own bespoke review flow, or the Section 28 "moderation bypass" threat
becomes trivially easy to introduce by accident in a future content
type. This module is deliberately generic (ContentType/GenericForeignKey
based) so it never needs to change shape when a new content type is
added later.
"""

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from core.models import TimestampedModel

class ModerationQueue(TimestampedModel):
    """
    Append-only queue of content awaiting/having undergone moderation.

    Deliberately inherits ONLY TimestampedModel, NOT
    core.models.SoftDeleteModel — a documented exception to core's
    "every future content model inherits both mixins" convention
    (P-011), per this part's own spec:

    A moderation audit/queue trail must remain permanent, append-only
    infrastructure. It is not user-facing content, so it has no business
    reason to ever be "soft-deleted" the way a Post or Comment would be;
    doing so would let a queue row disappear from the default `.objects`
    manager while the underlying content it refers to is still live,
    which is exactly the kind of accidental gap Section 28 warns about.
    ModerationLog (P-037) will document the same exception for the same
    reason when it lands.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    class Priority(models.TextChoices):
        NORMAL = "normal", "Normal"
        # Stories (Phase 8, P-047) use fast_path for their much shorter
        # (24h) content lifetime — this part only defines the choice
        # now so P-047 doesn't need a schema change later.
        FAST_PATH = "fast_path", "Fast path"

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="moderation_queue_entries",
    )
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.NORMAL,
    )

    class Meta:
        db_table = "moderation_moderationqueue"
        verbose_name = "Moderation queue entry"
        verbose_name_plural = "Moderation queue entries"
        indexes = [
            # Backs the generic-FK lookup ("has this specific object
            # already got a queue row?") used by the enqueue-once-on-
            # creation signal below.
            models.Index(fields=["content_type", "object_id"]),
            # Backs the moderator queue's future list-pending query
            # (P-038): "show me everything with status=pending".
            models.Index(fields=["status"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"ModerationQueue({self.content_type}, {self.object_id}, {self.status})"


class Moderatable(models.Model):
    """
    Abstract mixin every future content model (Post, Reel, Story — Phases
    7/8) inherits to participate in the shared moderation subsystem.

    CROSS-CUTTING CONTRACT — do not reinvent per content type:
    The exact field name ``status`` and its exact three choice values
    (``pending_review`` / ``published`` / ``rejected``) defined on THIS
    mixin are now load-bearing for other apps. In particular, Part
    P-043's ``.objects.published()`` manager filters on
    ``status == "published"`` from this exact field on whichever content
    model it's attached to. A future content type must inherit this
    mixin's ``status`` field as-is rather than defining its own
    similarly-named field with different choice values — doing so would
    silently break P-043's manager for that content type.

    ``status`` lives directly on the content model itself (not only via
    a ModerationQueue join) so the common "is this visible" check is a
    fast single-table filter, not a join through ModerationQueue on
    every feed/list query.

    Enqueueing a new ModerationQueue row on first creation is handled by
    a post_save signal receiver (moderation/signals.py), not a save()
    override on this mixin — see that module's docstring for why.
    """

    class Status(models.TextChoices):
        PENDING_REVIEW = "pending_review", "Pending review"
        PUBLISHED = "published", "Published"
        REJECTED = "rejected", "Rejected"

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING_REVIEW,
    )

    class Meta:
        abstract = True
        
    
    def get_moderation_preview(self):
        """
        Return a small, content-type-agnostic preview of this object for
        the moderator queue API (Part P-038).

        The moderation queue is generic: it does not know whether a row
        points at a Post, a Reel or a Story. Instead of hardcoding
        per-type logic in the serializer, the serializer calls this
        method on ``queue_item.content_object``.

        Contract: return a dict with EXACTLY these two keys:
            {"preview_text": str, "preview_image_url": str | None}

        This default works with no override (str(self) truncated to 200
        characters, no image), which keeps the queue API fully usable
        and testable before any real content type exists.

        Concrete subclasses (Post, Reel, Story — Phases 7/8) SHOULD
        override this to return something useful to a moderator (the
        real caption text, the real thumbnail URL). Leaving the generic
        fallback in production content is a gap, not a design choice.
        """
        return {
            "preview_text": str(self)[:200],
            "preview_image_url": None,
        }



class ModerationLog(TimestampedModel):
    """
    Permanent, append-only audit trail of every moderation decision
    (Part P-037): who reviewed which queue item, what they decided,
    and why.

    Deliberately inherits ONLY TimestampedModel, NOT
    core.models.SoftDeleteModel — the same documented exception as
    ModerationQueue (P-036), for the same reason: an audit trail must
    stay permanent. A soft-deleted log row would silently vanish from
    the default manager, and the rejection reason and reviewer identity
    must never be lost, even if the queue item is later cleaned up.

    Both foreign keys use on_delete=PROTECT so that neither a queue item
    nor a reviewer account can be deleted out from under an existing
    log entry.

    ``reason`` is blank=True at the database level because approvals
    carry no reason. A non-empty reason for rejections is enforced in
    the service layer (moderation/services.py), not as a DB constraint.

    Rows are created ONLY by moderation.services.approve()/reject().
    """

    class Action(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    queue_item = models.ForeignKey(
        ModerationQueue,
        on_delete=models.PROTECT,
        related_name="logs",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="moderation_logs",
    )
    action = models.CharField(
        max_length=20,
        choices=Action.choices,
    )
    reason = models.TextField(blank=True)

    class Meta:
        db_table = "moderation_moderationlog"
        verbose_name = "Moderation log entry"
        verbose_name_plural = "Moderation log entries"
        ordering = ["-created_at"]

    def __str__(self):
        return f"ModerationLog(queue_item={self.queue_item_id}, {self.action})"