from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """
    Notifications app (Part P-072 seam, completed in Part P-078).

    Follows the project-wide top-level-app convention (no ``apps/``
    package) - this app lives at ``notifications/``, not
    ``apps/notifications/``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "notifications"
    verbose_name = "Notifications"

    def ready(self):
        # Registers the post_save receiver that auto-creates a
        # NotificationPreference for every new User. Imported here
        # (not at module load time) per Django's standard convention
        # for wiring signal receivers, so app-registry/model loading
        # order is never a problem.
        import notifications.signals  # noqa: F401
