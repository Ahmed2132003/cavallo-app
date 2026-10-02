"""
Part P-092 (STEP 1): serializers for the monetization app's public API.

PlanSerializer is READ-ONLY output for the Web Dashboard's pricing table.
Only the five fields a buyer needs are exposed; timestamps and anything
else on Plan stay private. ``price`` is a DecimalField, so DRF renders it
as a STRING (e.g. "250.00") by default; the API contract documents this.
"""

from rest_framework import serializers

from monetization.models import Plan


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ["id", "name", "duration_days", "price", "currency"]
        read_only_fields = fields
