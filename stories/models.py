"""
stories app: Story model (Part P-046).

Architecture Section 6 names Stories as the platform's single most
time-sensitive content type: a Story can expire (24h TTL) before a
human ever reviews it, if moderation is slow. Everything about this
model's design (fast_path moderation priority via Part P-046's
moderation_priority hook, immediate enqueue-on-create — unlike Reel's
deferred pattern, and a stored-not-computed expires_at) exists to
minimize that risk window.

ADR-002: Stories live in their own genuinely separate app and table,
not merged into content.Post via a discriminator field. Do not add a
story_type field to content.models.Post as a shortcut for this.

SCOPE FINDING on media type (flagged explicitly per this part's own
instructions, not silently decided): the only source material this
part had access to (the project's presentation deck) describes Story
content as "صورة أو فيديو قصير" (a photo or a short video) with
"معقّد بالبداية Editing بدون" (no complex editing to start), and names
no processing/transcoding step for Story video, unlike Reel's (P-042)
explicit async-transcoding requirement. This model therefore proceeds
WITHOUT Reel's deferred-enqueue / processing_status machinery — a
Story auto-enqueues immediately on creation, exactly like Post. If a
genuine transcoding requirement for Story video surfaces later, that
is a scope change for a dedicated future part, not something silently
retrofitted here.

SCOPE FINDING on caption/text-overlay/product-attachment (also
flagged, not silently decided): the presentation deck's Trader
capabilities for Stories list "Add Text" and "Add Product / Link"
alongside Create/Delete. This part's own Detailed Implementation scope
paragraph, however, enumerates Story's fields exhaustively as
business/media/published_at/expires_at/status only, with no caption or
product-link field. Since this part's own scope list is the more
specific, authoritative source for what THIS model contains, no
caption/text_overlay/product_link field is added here — this is a real
gap between the presentation deck and this part's own written scope,
left open for Ahmed to resolve before any later part (Flutter
P-050/P-051, or a dedicated Story-editing part) assumes those fields
exist.
"""

from datetime import timedelta

from django.db import models
from django.utils import timezone

from businesses.models import BusinessProfile
from core.models import SoftDeleteModel, TimestampedModel
from moderation.models import Moderatable, ModerationQueue


class Story(Moderatable, TimestampedModel, SoftDeleteModel):
    """
    A trader/factory's ephemeral (24h) social content item — a photo or
    a short video. Deliberately no comments field or relationship at
    all — an explicit, architecture-mandated difference from Post/Reel
    (Parts P-041/P-042), not an oversight.

    ``status`` is inherited from Moderatable and MUST NOT be set
    directly anywhere in this app's code — exclusively managed by
    moderation.services.approve()/reject(), exactly as for Post/Reel.

    Unlike Reel, a Story does NOT defer its moderation enqueue —
    ``auto_enqueue_on_create`` keeps the Moderatable default of True
    (see the module docstring above for why: no transcoding step for
    MVP-scope Stories). The ONE deviation Story makes from Post/Reel is
    ``moderation_priority`` below: every Story's auto-created
    ModerationQueue row gets ``priority="fast_path"`` instead of the
    default ``"normal"``, via Part P-046's moderation_priority hook
    (see moderation/models.py's Moderatable docstring) — because a
    24h-TTL item sitting in a normal-priority queue risks expiring
    before any moderator ever sees it.
    """

    # Part P-046's fast_path priority hook (moderation/models.py).
    # Post/Reel do NOT set this — they keep the Moderatable default of
    # ModerationQueue.Priority.NORMAL unchanged.
    moderation_priority = ModerationQueue.Priority.FAST_PATH

    business = models.ForeignKey(
        BusinessProfile,
        on_delete=models.PROTECT,
        related_name="stories",
    )
    # Plain FileField, not ImageField/a dedicated "VideoField" — same
    # P-013/P-032/P-041/P-042 convention (no Pillow dependency in this
    # project). Real content-type + size validation happens in
    # stories/serializers.py's StorySerializer via core.media's
    # validate_upload(), same pattern as Post.image / Reel.video.
    media = models.FileField(upload_to="stories/media/")
    # Set ONCE, in save() below, on first creation only — never via
    # auto_now_add, so a custom save() override can fully control both
    # this and expires_at together from a single timezone.now() call
    # (see save() below).
    published_at = models.DateTimeField()
    # Computed and stored at creation time (published_at + 24h), not
    # computed dynamically on every read — this matters because the
    # future expiry sweep job and any visibility-check query need a
    # real indexed column to filter/sort on, not a computed Python-side
    # value (see the composite index below and this part's spec).
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [
            # Matches the architecture's Section 9 indexing strategy for
            # Story specifically — backs both a future expiry sweep
            # query and any "still-visible stories for this business"
            # check.
            #
            # Part P-046 FIX: named "story_biz_status_exp_idx" (24
            # chars), not "stories_story_biz_status_exp_idx" (32
            # chars) — Django's models.E034 enforces a hard 30-char
            # ceiling on every index/constraint name regardless of the
            # actual database backend in use (kept portable to Oracle),
            # so the original name failed `manage.py check` before any
            # migration could even be generated.
            models.Index(
                fields=["business", "status", "expires_at"],
                name="story_biz_status_exp_idx",
            ),
        ]

    def __str__(self):
        return f"Story({self.pk}) by {self.business_id}"

    def save(self, *args, **kwargs):
        """
        published_at/expires_at are set ONCE, on first creation, and
        never recomputed on subsequent saves (e.g. a moderator's
        approve()/reject() call, which re-saves this row via
        moderation.services — that must never push expires_at
        forward). Uses self._state.adding (Django's own "is this an
        INSERT" flag), not `self.pk is None`, so it stays correct even
        for a model with a manually-assigned primary key.
        """
        if self._state.adding and self.published_at is None:
            self.published_at = timezone.now()
        if self._state.adding and self.expires_at is None:
            self.expires_at = self.published_at + timedelta(hours=24)
        super().save(*args, **kwargs)

    def get_moderation_preview(self):
        return {
            "preview_text": f"Story by {self.business.business_name}",
            "preview_image_url": self.media.url if self.media else None,
        }
