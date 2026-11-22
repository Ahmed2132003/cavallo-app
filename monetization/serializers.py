"""
Part P-092 (STEP 1): serializers for the monetization app's public API.

PlanSerializer is READ-ONLY output for the Web Dashboard's pricing table.
Only the five fields a buyer needs are exposed; timestamps and anything
else on Plan stay private. ``price`` is a DecimalField, so DRF renders it
as a STRING (e.g. "250.00") by default; the API contract documents this.
"""

from rest_framework import serializers

from monetization.models import Plan
from monetization.services import parse_gb


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        # First five = legacy contract (unchanged). The rest are BE-01 additions.
        fields = [
            "id",
            "name",
            "duration_days",
            "price",
            "currency",
            "code",
            "name_ar",
            "name_en",
            "price_egp",
            "storage_bytes",
            "is_featured",
            "trial_days",
            "is_custom",
            "sort_order",
        ]
        read_only_fields = fields


class CustomQuoteRequestSerializer(serializers.Serializer):
    """
    BE-03 input: ``gb``. Kept as text until parse_gb() so floats never
    enter the price math. Range checks live in calculate_custom_price().
    """

    gb = serializers.CharField()

    def validate(self, attrs):
        attrs["gb"] = parse_gb(attrs["gb"])
        return attrs


class CustomQuoteResponseSerializer(serializers.Serializer):
    """BE-03 output. Decimals render as strings ("630.00"), like Plan.price."""

    gb = serializers.DecimalField(max_digits=8, decimal_places=2)
    price_egp = serializers.DecimalField(max_digits=10, decimal_places=2)
    quota_bytes = serializers.IntegerField()
    is_featured = serializers.BooleanField()
