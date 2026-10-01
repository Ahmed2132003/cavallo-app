from rest_framework import serializers

from analytics.models import BusinessDailyStats


class BusinessDailyStatsSerializer(serializers.ModelSerializer):
    """
    Part P-084. Read-only wire shape of one daily rollup row.

    Exactly the four metrics this system genuinely tracks, plus the date.
    There is deliberately NO product-views / profile-views field: that
    activity is not tracked anywhere (documented gap, not a bug).
    """

    class Meta:
        model = BusinessDailyStats
        fields = (
            "date",
            "new_followers",
            "total_likes_received",
            "total_comments_received",
            "total_story_views",
        )
        read_only_fields = fields


class DailyStatsQuerySerializer(serializers.Serializer):
    """
    Query-string validation for GET /api/v1/analytics/business/{id}/daily/.

    Both bounds are optional, inclusive, ISO ``YYYY-MM-DD``. Unknown query
    params (``cursor``, ``page_size``) are ignored here; the paginator reads
    them itself.
    """

    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs):
        date_from = attrs.get("date_from")
        date_to = attrs.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise serializers.ValidationError(
                {"date_from": ["date_from must be on or before date_to."]}
            )
        return attrs