from django.apps import AppConfig


class DevicesConfig(AppConfig):
    """
    Devices app (Part P-081): FCM device-token registration.

    Follows the project-wide top-level-app convention (no ``apps/``
    package) - this app lives at ``devices/``, not ``apps/devices/``,
    even though the master plan text writes ``apps/devices/...``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "devices"
    verbose_name = "Devices"
