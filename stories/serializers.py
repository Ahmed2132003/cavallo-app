from rest_framework import serializers

from core.media import validate_upload
from stories.models import Story


class StorySerializer(serializers.ModelSerializer):
    """
    Part P-046. Write field: media only. id/business/published_at/
    expires_at/status are all read-only — business resolved
    server-side from request.user.business_profile in the view (same
    pattern as PostSerializer/ReelSerializer); published_at/expires_at
    are computed once by Story.save() (see stories/models.py) and must
    never be client-writable; status is exclusively managed by
    moderation.services.approve()/reject().

    Deliberately no rejection_reason field (unlike PostSerializer/
    ReelSerializer, P-044) — this part's own scope covers only the
    model plus a minimal creation endpoint; the owner-facing
    rejection-reason surfacing PostSerializer/ReelSerializer got in
    P-044 is not part of this part's Definition of Done and is left as
    a gap for a future part, not silently added here.

    validate_media() accepts both image and video MIME types (a Story
    can be either — see stories/models.py's module docstring) and uses
    Reel's 100 MB ceiling rather than Post's 5 MB one, since a video
    Story must fit under it. Like ReelSerializer's own docstring says
    about its identical number: this is a placeholder ceiling (neither
    this part's spec nor the source material gives an exact number for
    Story specifically); revisit once real product limits are decided.
    """

    class Meta:
        model = Story
        fields = (
            "id",
            "business",
            "media",
            "published_at",
            "expires_at",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "business",
            "published_at",
            "expires_at",
            "status",
            "created_at",
            "updated_at",
        )

    def validate_media(self, value):
        validate_upload(
            value,
            allowed_mime_types=[
                "image/jpeg",
                "image/png",
                "image/webp",
                "video/mp4",
                "video/quicktime",
                "video/webm",
            ],
            max_size_bytes=100 * 1024 * 1024,
        )
        return value
