"""
Serializers for Part P-082 - notification-center REST API.

NotificationSerializer is read-only on purpose: the only write a client
may make to a Notification is "mark as read", and that goes through a
dedicated endpoint (NotificationMarkReadView), never through a generic
update. ``recipient`` is deliberately NOT exposed: every list is already
scoped to request.user, so echoing the id would add nothing.

NotificationPreferenceSerializer exposes exactly the three P-078
category toggles. ``user`` / ``id`` are not declared fields, so a forged
value for either in a PATCH body is silently dropped during validation
(same IDOR-by-construction rule as businesses/me/, P-026).
"""

from rest_framework import serializers

from notifications.models import Notification, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id",
            "notification_type",
            "title",
            "body",
            "deep_link_type",
            "target_id",
            "is_read",
            "created_at",
        ]
        read_only_fields = fields


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = [
            "chat_notifications_enabled",
            "moderation_notifications_enabled",
            "social_notifications_enabled",
        ]
