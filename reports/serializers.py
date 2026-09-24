from rest_framework import serializers

from reports.models import Report

# PLACEHOLDER (spec is silent): prevents unbounded free text. Tunable.
REPORT_DETAILS_MAX_LENGTH = 1000

# Largest value PositiveIntegerField / PostgreSQL integer can hold.
OBJECT_ID_MAX = 2**31 - 1


class ReportCreateSerializer(serializers.Serializer):
    """Input validation only. The whitelist/target checks live in targets.py."""

    content_type = serializers.CharField()
    object_id = serializers.IntegerField(min_value=1, max_value=OBJECT_ID_MAX)
    reason = serializers.ChoiceField(choices=Report.Reason.choices)
    details = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=REPORT_DETAILS_MAX_LENGTH,
    )
