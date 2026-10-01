from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    """
    Part P-084. Top-level app (``analytics/``, not ``apps/analytics/``),
    per the project-wide convention in force since P-011.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "analytics"