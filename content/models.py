from django.db import models

from businesses.models import BusinessProfile
from core.models import SoftDeleteModel, TimestampedModel
from moderation.models import Moderatable


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
    caption = models.TextField()
    image = models.FileField(upload_to="posts/", null=True, blank=True)

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