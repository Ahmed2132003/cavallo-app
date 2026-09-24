"""
Serializers for Part P-054 — Save list output.

Follow/Like don't have serializers (their views return a plain
{"following"/"liked": bool} dict) — Save is the first social/ feature
that needs a real list representation.
"""

from rest_framework import serializers

from .models import Comment, Save


def _preview_for(content_object):
    """
    Content-type-agnostic preview, same two-key contract as
    Moderatable.get_moderation_preview() (moderation/models.py):
    {"preview_text": str, "preview_image_url": str|None}.

    Post/Reel already implement get_moderation_preview() (they're
    Moderatable) — reused directly. Product is NOT Moderatable (P-031:
    SoftDeleteModel only), so it gets an equivalent, hand-written
    fallback here rather than adding get_moderation_preview() to a
    model that has nothing to do with the moderation queue.
    """
    if hasattr(content_object, "get_moderation_preview"):
        return content_object.get_moderation_preview()

    # Product fallback.
    return {
        "preview_text": (content_object.name or "")[:200],
        "preview_image_url": (
            content_object.image.url if content_object.image else None
        ),
    }


class SaveSerializer(serializers.ModelSerializer):
    content_type = serializers.SerializerMethodField()
    preview = serializers.SerializerMethodField()

    class Meta:
        model = Save
        fields = ("id", "content_type", "object_id", "preview", "created_at")
        read_only_fields = fields

    def get_content_type(self, obj):
        # ContentType.model is already the lowercase model name
        # ("post"/"reel"/"product") — matches SAVE_ALLOWED_CONTENT_TYPES'
        # keys directly, no separate reverse-lookup table needed.
        return obj.content_type.model

    def get_preview(self, obj):
        content_object = obj.content_object
        if content_object is None:
            # Target row was hard-deleted; content_object resolves to
            # None rather than raising. Surface a null preview instead
            # of a 500 — the Save row itself is still valid data.
            return None
        return _preview_for(content_object)


# Part P-055 — PLACEHOLDER max comment length (the spec is silent on it;
# an unbounded TextField would let a single request store megabytes).
# Tunable; not a product-confirmed value.
COMMENT_MAX_LENGTH = 1000


class CommentCreateSerializer(serializers.Serializer):
    """
    Input validation for POST /api/v1/comments/. `content_type` is only
    checked as a non-empty string here; the closed whitelist check
    (post/reel) lives in the view, next to COMMENT_ALLOWED_CONTENT_TYPES.
    """

    content_type = serializers.CharField()
    object_id = serializers.IntegerField(min_value=1)
    text = serializers.CharField(max_length=COMMENT_MAX_LENGTH)


class CommentSerializer(serializers.ModelSerializer):
    """
    Public representation of a Comment. `reports_count` is deliberately
    NOT exposed. `is_hidden` is included so that the author/moderators
    (the only viewers who ever receive a hidden comment, see Part
    P-055's list endpoint) can render a "hidden" marker.
    """

    content_type = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = (
            "id",
            "user",
            "content_type",
            "object_id",
            "text",
            "is_hidden",
            "created_at",
        )
        read_only_fields = fields

    def get_content_type(self, obj):
        return obj.content_type.model


class CommentListQuerySerializer(serializers.Serializer):
    """Query-string validation for GET /api/v1/comments/."""

    content_type = serializers.CharField()
    object_id = serializers.IntegerField(min_value=1)
