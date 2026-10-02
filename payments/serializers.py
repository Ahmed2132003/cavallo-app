"""
Part P-092 (STEP 2): request serializer for POST /api/v1/payments/initiate/.

The ONLY accepted input is ``plan_id``. It is resolved to a Plan row here
(unknown id -> 400 with the error under ``fields.plan_id``). Anything else
in the body (for example a ``business_id``) is not a declared field and is
silently dropped, so the buyer can never be chosen by the client.
"""

from rest_framework import serializers

from monetization.models import Plan


class PaymentInitiateSerializer(serializers.Serializer):
    plan_id = serializers.PrimaryKeyRelatedField(
        queryset=Plan.objects.all(), source="plan"
    )
