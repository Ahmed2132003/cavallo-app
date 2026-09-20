"""
Serializers for the moderator queue API (Part P-038).

The moderation queue is generic (ContentType/GenericForeignKey), so this
serializer must never contain per-content-type logic for Post, Reel or
Story. Anything content-specific comes from the content object itself
through ``Moderatable.get_moderation_preview()`` (see
moderation/models.py), which each concrete content model overrides.
"""

from django.utils import timezone
from rest_framework import serializers

from moderation.models import ModerationQueue


class ModerationQueueSerializer(serializers.ModelSerializer):
    """
    Read-only, content-type-agnostic summary of one moderation queue row.

    ``content_type`` is the model name (e.g. "post"). ``age`` is the
    whole number of seconds since the item was queued, so a moderator
    client can show how stale an item is. ``preview`` is the dict
    returned by the content object's ``get_moderation_preview()`` (or
    ``None`` if the content row no longer exists). ``submitter`` is
    included only when the content object exposes a ``business``
    attribute; otherwise the key is omitted entirely.
    """

    content_type = serializers.CharField(source="content_type.model", read_only=True)
    age = serializers.SerializerMethodField()
    preview = serializers.SerializerMethodField()
    submitter = serializers.SerializerMethodField()

    class Meta:
        model = ModerationQueue
        fields = (
            "id",
            "content_type",
            "object_id",
            "status",
            "priority",
            "created_at",
            "age",
            "preview",
            "submitter",
        )
        read_only_fields = fields

    def get_age(self, obj):
        seconds = (timezone.now() - obj.created_at).total_seconds()
        return max(0, int(seconds))

    def get_preview(self, obj):
        content = obj.content_object
        if content is None:
            return None
        return content.get_moderation_preview()

    def get_submitter(self, obj):
        # Not every future content type is guaranteed to have a
        # ``business`` attribute, so it is looked up defensively.
        business = getattr(obj.content_object, "business", None)
        if business is None:
            return None
        return {"business_name": getattr(business, "business_name", str(business))}

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("submitter") is None:
            data.pop("submitter", None)
        return data


class RejectRequestSerializer(serializers.Serializer):
    """
    Validates the body of POST /queue/{id}/reject/ (Part P-038).

    A rejection reason is mandatory: a missing or blank ``reason``
    produces a normal 400 in the P-012 error envelope (with the field
    name under ``fields``) instead of reaching the service layer.
    """

    reason = serializers.CharField()
