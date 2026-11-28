from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from analytics import constants as C


class ViewEventSerializer(serializers.Serializer):
    """
    Part BI-02. Body of POST /api/v1/analytics/events/views/.

    There is deliberately NO counter field and no ``source``/``viewer_key``
    here: the server derives the viewer from the token (or the hashed
    anon_id) and every counter is rebuilt from raw events.
    """

    event_id = serializers.UUIDField()
    target_type = serializers.ChoiceField(choices=C.TARGET_TYPES)
    target_id = serializers.IntegerField(min_value=1)
    # Optional client time (offline queue). Bounded below.
    occurred_at = serializers.DateTimeField(required=False)
    watch_ms = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=C.MAX_WATCH_MS
    )
    anon_id = serializers.RegexField(r"^[A-Za-z0-9._:\-]{16,128}$", required=False)

    def validate_occurred_at(self, value):
        now = timezone.now()
        if value > now + timedelta(minutes=C.EVENT_FUTURE_TOLERANCE_MINUTES):
            raise serializers.ValidationError("occurred_at cannot be in the future.")
        if value < now - timedelta(hours=C.EVENT_MAX_AGE_HOURS):
            raise serializers.ValidationError(
                f"occurred_at is older than {C.EVENT_MAX_AGE_HOURS} hours."
            )
        return value

    def validate(self, attrs):
        if attrs.get("watch_ms") is not None and attrs["target_type"] != C.TARGET_REEL:
            raise serializers.ValidationError(
                {"watch_ms": ["watch_ms is only accepted for reels."]}
            )
        request = self.context.get("request")
        authenticated = bool(request and request.user and request.user.is_authenticated)
        if authenticated:
            attrs.pop("anon_id", None)
        elif not attrs.get("anon_id"):
            raise serializers.ValidationError(
                {"anon_id": ["anon_id is required for anonymous viewers."]}
            )
        return attrs
