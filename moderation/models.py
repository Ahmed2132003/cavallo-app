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

    ``auto_enqueue_on_create`` (Part P-042) — DEFERRED-ENQUEUE HOOK:
    By default (``True``), the signal in moderation/signals.py creates
    exactly one ModerationQueue row the first time any Moderatable
    subclass is saved (see that module's docstring). This is correct
    for content that is meaningful to review immediately on creation
    (e.g. Post: a caption + an already-usable image).

    It is NOT correct for content whose first save merely represents
    "raw input received, not yet processed" — Reel (P-042) is the first
    such case: a freshly-uploaded raw video is not something a moderator
    should ever see; it must be transcoded first. A concrete subclass
    that needs this sets the class attribute:

        class Reel(Moderatable, ...):
            auto_enqueue_on_create = False

    and is then responsible for calling
    ``ModerationQueue.objects.create(...)`` itself, explicitly, once the
    object actually reaches a reviewable state (see
    content/tasks.py's ``transcode_reel``).

    This is deliberately a class attribute on the mixin (checked via
    ``getattr(instance, "auto_enqueue_on_create", True)`` in the
    signal), NOT a change to the signal's ``isinstance`` check for a
    named model. moderation/ must never import or know about a specific
    content type (Post/Reel/Story) — that is exactly what the generic,
    sender-less signal was built to avoid (see signals.py). The default
    of ``True`` means every model that inherits Moderatable today
    (Post, DummyContent) keeps its exact existing behavior unchanged;
    only a subclass that explicitly opts out is affected.

    ``moderation_priority`` (Part P-046) — FAST_PATH PRIORITY HOOK:
    By default (``ModerationQueue.Priority.NORMAL``), the signal in
    moderation/signals.py creates the auto-enqueued ModerationQueue row
    with ``priority="normal"`` — the same behavior as before this hook
    existed. A concrete subclass whose content has a short lifetime and
    must be reviewed urgently sets the class attribute:

        class Story(Moderatable, ...):
            moderation_priority = ModerationQueue.Priority.FAST_PATH

    This is a genuinely different kind of deviation from
    ``auto_enqueue_on_create``: Story does NOT defer its enqueue (it
    keeps the default immediate enqueue-on-create), it only changes the
    priority value that immediate enqueue uses. Like
    ``auto_enqueue_on_create``, this is read via
    ``getattr(instance, "moderation_priority", ModerationQueue.Priority.NORMAL)``
    in the signal, NOT a change to the signal's generic isinstance
    check, and NOT a model field (it must never appear as a DB column
    or migration).
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

    # See the class docstring's "DEFERRED-ENQUEUE HOOK" section (P-042).
    # Not a model field on purpose — it must never appear as a DB column
    # or migration; it is read only by moderation/signals.py via getattr().
    auto_enqueue_on_create = True

    # PRIORITY HOOK (Part P-046) — same pattern as auto_enqueue_on_create
    # above: a plain class attribute, read via getattr() in
    # moderation/signals.py, NOT a model field and NOT a change to this
    # module's isinstance-only, content-type-agnostic signal logic.
    # moderation/ must still never import or know about a specific
    # content type (Post/Reel/Story).
    #
    # Default is ModerationQueue.Priority.NORMAL, matching the
    # ModerationQueue.priority field's own default — every model that
    # inherits Moderatable today (Post, Reel, DummyContent) keeps its
    # exact existing behavior, 'normal' priority, completely unchanged.
    # Story (Phase 8, P-046) is the first concrete subclass to override
    # this, to ModerationQueue.Priority.FAST_PATH, because of its 24h
    # TTL (architecture Section 6's named risk: a Story can expire
    # before a human ever reviews it if moderation is slow). See this
    # class's own docstring above for the full rationale.
    moderation_priority = ModerationQueue.Priority.NORMAL

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