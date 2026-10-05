"""
Serializers for Part P-081 - device-token registration.

A plain Serializer (not a ModelSerializer) on purpose: a ModelSerializer
would attach a UniqueValidator to ``token`` and reject a re-registration
of an existing token with a 400, which is exactly the case the endpoint
must ACCEPT (token refresh, or a different user signing in on the same
device). Uniqueness is handled by the upsert in the view instead.
"""

from rest_framework import serializers

from devices.models import DeviceToken


class DeviceRegisterSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=512, trim_whitespace=True)
    platform = serializers.ChoiceField(
        choices=[value for value, _label in DeviceToken.PLATFORM_CHOICES]
    )
