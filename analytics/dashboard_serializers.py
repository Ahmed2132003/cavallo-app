from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from analytics import constants as C
from analytics.models import ExternalContentMetricSnapshot


class DateRangeQuerySerializer(serializers.Serializer):
    """
    ?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&period=7d|30d|90d

    date_from/date_to are inclusive UTC dates. period is only used when
    date_from is missing (window of that many days ending at date_to).
    Cross-field checks (order, 366-day cap) are in metrics.resolve_range.
    Any other query key (e.g. business_id) is ignored, never trusted.
    """

    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    period = serializers.ChoiceField(choices=list(C.PERIOD_DAYS), required=False)


class ProductListQuerySerializer(DateRangeQuerySerializer):
    ordering = serializers.CharField(required=False, max_length=30)
    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=C.MAX_PAGE_SIZE,
        default=C.DEFAULT_PAGE_SIZE,
    )


class ContentListQuerySerializer(ProductListQuerySerializer):
    type = serializers.ChoiceField(choices=["post", "reel", "all"], default="all")


class OrdersSummaryQuerySerializer(DateRangeQuerySerializer):
    top = serializers.IntegerField(required=False, min_value=1, max_value=20, default=5)


class ExternalMetricInputSerializer(serializers.Serializer):
    """
    Owner-entered external number. ``source`` is NOT accepted: manual entry
    is always stored with source="manual".
    """

    platform = serializers.ChoiceField(
        choices=ExternalContentMetricSnapshot.Platform.choices
    )
    metric_type = serializers.ChoiceField(
        choices=ExternalContentMetricSnapshot.MetricType.choices
    )
    metric_value = serializers.IntegerField(min_value=0, max_value=10**12)
    captured_at = serializers.DateTimeField(required=False)
    external_reference = serializers.CharField(
        required=False, allow_blank=True, max_length=255
    )

    def validate_captured_at(self, value):
        if value > timezone.now() + timedelta(minutes=C.EVENT_FUTURE_TOLERANCE_MINUTES):
            raise serializers.ValidationError("captured_at cannot be in the future.")
        return value
