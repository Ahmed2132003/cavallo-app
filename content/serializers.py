from rest_framework import serializers

from content.models import Post
from core.media import validate_upload


class PostSerializer(serializers.ModelSerializer):
    """
    Write fields: caption, image. Everything else (id, business, status,
    timestamps) is read-only — `business` is resolved server-side from
    request.user.business_profile in the view (P-032's exact pattern),
    and `status` is exclusively managed by moderation.services.approve()/
    reject(), never writable through this app's own serializer.
    """

    class Meta:
        model = Post
        fields = (
            "id",
            "business",
            "caption",
            "image",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "business", "status", "created_at", "updated_at")

    def validate_image(self, value):
        if value:
            validate_upload(
                value,
                allowed_mime_types=["image/jpeg", "image/png", "image/webp"],
                max_size_bytes=5 * 1024 * 1024,
            )
        return value