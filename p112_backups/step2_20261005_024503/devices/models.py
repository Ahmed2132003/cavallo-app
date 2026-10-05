"""
devices app models (Part P-081).

One model lives here: DeviceToken - one row per FCM registration token.

The token is GLOBALLY unique, not unique per user. A token identifies a
physical app installation, and one installation can change hands (user A
logs out, user B logs in on the same phone; or the app is reinstalled
and a new account signs in). Registering an existing token therefore
moves it to the new owner instead of erroring - see
devices/views.py::DeviceRegisterView.
"""

from django.conf import settings
from django.db import models

from core.models import TimestampedModel


class DeviceToken(TimestampedModel):
    """
    One FCM device token owned by exactly one user at a time.

    A user may own many tokens (phone + tablet, or several installs).
    ``notifications.services.send_push_notification`` (P-081, next
    step) sends to every token the recipient owns.
    """

    PLATFORM_IOS = "ios"
    PLATFORM_ANDROID = "android"
    PLATFORM_CHOICES = [
        (PLATFORM_IOS, "iOS"),
        (PLATFORM_ANDROID, "Android"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens",
    )
    # FCM registration tokens are ~160-180 characters today; 512 leaves
    # generous headroom without risking an index-size problem.
    token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        # Never print the full token: it is a credential-like value
        # that would otherwise leak into logs and the admin.
        return f"{self.platform}:{self.token[:8]}... (user {self.user_id})"
