"""
Views for Part P-081 - device-token registration.

POST /api/v1/devices/register/  {"token": "...", "platform": "ios"|"android"}

Upsert semantics (the whole point of this endpoint):

* token not seen before      -> create a row for request.user (HTTP 201).
* token already exists       -> update that row in place: owner becomes
  request.user, platform is refreshed (HTTP 200). This covers three
  real cases without any of them being an error:
    1. the app re-registers on every launch / onTokenRefresh with the
       same token (no actual change);
    2. user B logs in on a device that user A used (the token moves to
       B, so A stops receiving B's pushes);
    3. the same user reinstalls the app (rare: same token again).

Authenticated users only. The owner is ALWAYS request.user, never a
user id from the body, so a client cannot register a token onto someone
else's account (same IDOR rule as businesses/me/, architecture
Section 5 rule 10).
"""

from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from devices.models import DeviceToken
from devices.serializers import DeviceRegisterSerializer


class DeviceRegisterView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = DeviceRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data["token"]
        platform = serializer.validated_data["platform"]

        with transaction.atomic():
            device, created = DeviceToken.objects.update_or_create(
                token=token,
                defaults={"user": request.user, "platform": platform},
            )

        return Response(
            {"id": device.id, "platform": device.platform},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
