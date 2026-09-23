from django.db import models

from businesses.models import BusinessProfile
from core.models import SoftDeleteModel, TimestampedModel
from moderation.models import Moderatable


class PublishedManager(models.Manager):
    """
    Part P-043 — the single, canonical "what's actually visible"
    filter every future public-facing content endpoint must use.

    Filters on TWO conditions together, deliberately:
      - status == Moderatable.Status.PUBLISHED
      - is_deleted == False (SoftDeleteModel)
    A soft-deleted-but-approved Post/Reel must never appear here even
    though `.objects` (SoftDeleteModel's own manager) already excludes
    soft-deleted rows — this manager re-states that condition
    explicitly so it never silently depends on which base manager a
    future subclass happens to keep as `.objects`.

    Added as an EXTRA manager (`published_objects`), never as the
    default `.objects` — internal code (moderation queue, the owner's
    own list/edit views from P-041/P-042) legitimately needs to see
    pending/rejected items too, and making this the default would
    force every internal query to remember to override it, i.e. the
    exact bug-prone pattern this manager exists to prevent, inverted.
    """

    def get_queryset(self):
        return super().get_queryset().filter(
            status=Moderatable.Status.PUBLISHED,
            is_deleted=False,
        )


class Post(Moderatable, TimestampedModel, SoftDeleteModel):
    """
    A trader/factory's social content item (caption + image), the first
    real (non-throwaway) Moderatable content type.

    `status` is inherited from `Moderatable` and MUST NOT be set directly
    anywhere in this app's code — it is exclusively managed by
    `moderation.services.approve()`/`reject()`, triggered through the
    moderator queue API (P-038). A newly created Post is `pending_review`
    and is never publicly listed until approved (the public "visible
    posts" endpoint is P-043's `published()` manager, shared with Reel).
    """

    business = models.ForeignKey(
        BusinessProfile,
        on_delete=models.PROTECT,
        related_name="posts",
    )
    published_objects = PublishedManager()
    caption = models.TextField()
    image = models.FileField(upload_to="posts/", null=True, blank=True)
    likes_count = models.PositiveIntegerField(default=0)
    
    class Meta:
        indexes = [
            models.Index(fields=["business"], name="content_post_business_idx"),
        ]

    def __str__(self):
        return f"Post({self.pk}) by {self.business_id}"

    def get_moderation_preview(self):
        return {
            "preview_text": self.caption[:200],
            "preview_image_url": self.image.url if self.image else None,
        }


class ReelPublishedManager(PublishedManager):
    """
    Part P-043. Reel-only extra condition: also require
    processing_status == Reel.ProcessingStatus.READY.

    Belt-and-suspenders — normal flow should never produce an
    approved-but-still-processing Reel (Reel.auto_enqueue_on_create is
    False specifically so nothing gets queued for moderation before
    transcode_reel() reaches "ready" and creates the ModerationQueue
    row itself — see content/models.py's Reel docstring and
    moderation/models.py's Moderatable docstring). This condition
    exists purely as a second line of defense against that invariant
    ever being violated by a future bug, not because the normal flow
    can currently produce that state.
    """

    def get_queryset(self):
        return super().get_queryset().filter(
            processing_status=Reel.ProcessingStatus.READY,
        )


class Reel(Moderatable, TimestampedModel, SoftDeleteModel):
    """
    A trader/factory's short-video content item (Part P-042) — same
    Moderatable/ownership shape as Post, but with a genuinely separate
    technical concern: raw uploaded video needs asynchronous transcoding
    (format/resolution normalization + thumbnail extraction) before it
    is meaningful for a moderator to review, so it cannot use Post's
    "ready to moderate the instant it's created" behavior.

    TWO SEPARATE STATE MACHINES exist on this model. They must never be
    conflated:
      - `status` (inherited from Moderatable): pending_review / published
        / rejected — the MODERATION state. Exclusively managed by
        `moderation.services.approve()`/`reject()`, exactly as for Post.
      - `processing_status` (defined here): uploaded / processing / ready
        / failed — the TRANSCODING state. Exclusively managed by
        `content.tasks.transcode_reel()`.

    DEFERRED-ENQUEUE (this part is the first, deliberate exception to
    P-036's universal "Moderatable auto-enqueues into moderation on
    first save" rule): a freshly created Reel is raw, unprocessed
    video — not something a moderator should ever see. Setting
    `auto_enqueue_on_create = False` below stops
    `moderation/signals.py`'s post_save receiver from creating a
    ModerationQueue row on creation (see the full mechanism and
    rationale documented on `Moderatable` itself, in
    moderation/models.py — this is Part P-042's hook, added there in a
    prior step of this same part).

    `content.tasks.transcode_reel()` creates the ModerationQueue row
    itself, explicitly, once — and only once — `processing_status`
    reaches `"ready"`. A failed transcode never gets a queue row: there
    is nothing useful yet for a moderator to review.
    """

    class ProcessingStatus(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    # Part P-042's deferred-enqueue hook (see moderation/models.py's
    # Moderatable docstring). Post does NOT set this — it keeps the
    # default `True` from Moderatable unchanged.
    auto_enqueue_on_create = False

    business = models.ForeignKey(
        BusinessProfile,
        on_delete=models.PROTECT,
        related_name="reels",
    )
    caption = models.TextField()
    # Plain FileField, not ImageField/a dedicated "VideoField" — same
    # P-013/P-032/P-041 convention (no Pillow dependency in this
    # project, one shared validate_upload() for all content-type
    # checking instead of a per-field Django media class). Real
    # jpeg/png-vs-mp4 content-type + size validation happens in
    # content/serializers.py's ReelSerializer via core.media's
    # validate_upload(), same pattern as Post.image.
    video = models.FileField(upload_to="reels/videos/")
    # Populated by content.tasks.transcode_reel() once transcoding
    # succeeds; null=True/blank=True because it does not exist yet on
    # the initial raw upload.
    thumbnail = models.FileField(
        upload_to="reels/thumbnails/", null=True, blank=True
    )
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    processing_status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.UPLOADED,
    )
    published_objects = ReelPublishedManager()
    likes_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["business"], name="content_reel_business_idx"),
        ]

    def __str__(self):
        return f"Reel({self.pk}) by {self.business_id}"

    def get_moderation_preview(self):
        return {
            "preview_text": self.caption[:200],
            "preview_image_url": self.thumbnail.url if self.thumbnail else None,
        }