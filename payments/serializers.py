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


class SubscriptionCheckoutSerializer(serializers.Serializer):
    """
    BE-07: body of POST subscriptions/checkout/. Only ``plan`` (a Plan
    code) and ``gb`` are read; any amount/price/business field sent by
    the client is not declared, so it is dropped. ``gb`` is validated by
    payments.services.quote_storage_plan() (monetization.parse_gb).
    """

    plan = serializers.CharField(max_length=20)
    gb = serializers.JSONField(required=False, allow_null=True)
