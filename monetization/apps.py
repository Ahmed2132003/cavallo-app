from django.apps import AppConfig


class MonetizationConfig(AppConfig):
    """
    Part P-086. Top-level app (``monetization/``, not
    ``apps/monetization/``), per the project-wide convention in force
    since P-011.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "monetization"

    def ready(self):
        # BE-02: Free subscription on BusinessProfile creation.
        import monetization.signals  # noqa: F401

        # BE-04: storage usage ledger kept in sync with uploads/deletes.
        import monetization.storage_signals  # noqa: F401
