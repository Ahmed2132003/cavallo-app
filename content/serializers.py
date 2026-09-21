from rest_framework import serializers

from content.models import Post, Reel
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


class ReelSerializer(serializers.ModelSerializer):
    """
    Part P-042. Write fields: caption, video — that is all. `thumbnail`,
    `duration_seconds` and `processing_status` are read-only here
    because exactly one thing is allowed to set them:
    content.tasks.transcode_reel(). Letting a client PATCH any of the
    three directly would let it fake a "ready" Reel with a fabricated
    thumbnail/duration that never actually went through ffmpeg.

    `business` and `status` are read-only for the exact same reasons as
    PostSerializer above (business resolved server-side in the view;
    status exclusively owned by moderation.services).

    Known, deliberately out-of-scope limitation (flag for a future
    part, not silently "fixed" here): PATCHing `video` on an existing
    Reel replaces the file but does NOT re-trigger transcode_reel, so
    thumbnail/duration_seconds/processing_status would silently go
    stale against the new file. This part's spec only covers the
    create-time pipeline; re-transcoding on update was never in scope.
    """

    class Meta:
        model = Reel
        fields = (
            "id",
            "business",
            "caption",
            "video",
            "thumbnail",
            "duration_seconds",
            "processing_status",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "business",
            "thumbnail",
            "duration_seconds",
            "processing_status",
            "status",
            "created_at",
            "updated_at",
        )

    def validate_video(self, value):
        # Same P-013 content-sniffing choke point as PostSerializer's
        # validate_image — real MIME detection via libmagic, not the
        # filename extension or client-supplied Content-Type. 100 MB
        # is a placeholder ceiling (this part's spec gives no exact
        # number); revisit once real product limits are decided.
        validate_upload(
            value,
            allowed_mime_types=["video/mp4", "video/quicktime", "video/webm"],
            max_size_bytes=100 * 1024 * 1024,
        )
        return value