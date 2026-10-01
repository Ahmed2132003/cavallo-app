from django.apps import AppConfig


class MonetizationConfig(AppConfig):
    """
    Part P-086. Top-level app (``monetization/``, not
    ``apps/monetization/``), per the project-wide convention in force
    since P-011.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "monetization"