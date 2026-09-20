from django.apps import AppConfig


class ModerationConfig(AppConfig):
    """
    Part P-036. Follows the project-wide top-level-app convention
    established in P-011/P-012/P-013/P-016/P-024/P-025 (no ``apps/``
    package) — this app lives at ``moderation/``, not
    ``apps/moderation/``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "moderation"

    def ready(self):
        # Registers the generic post_save receiver that enqueues a
        # ModerationQueue row for any newly-created Moderatable
        # instance. Imported here (not at module load time), matching
        # categories/apps.py's established pattern for wiring signal
        # receivers, so app-registry/model loading order is never a
        # problem.
        import moderation.signals  # noqa: F401
