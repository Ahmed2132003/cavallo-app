from django.apps import AppConfig


class BusinessesConfig(AppConfig):
    """
    Part P-024. Follows the project-wide top-level-app convention
    established in P-011/P-012/P-013/P-016 (no apps/ package) — this app
    lives at businesses/, not apps/businesses/.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "businesses"