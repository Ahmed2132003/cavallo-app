from django.apps import AppConfig


class ProductsConfig(AppConfig):
    """
    Part P-031. Follows the project-wide top-level-app convention
    established in P-011/P-012/P-013/P-016/P-024/P-025 (no apps/
    package) - this app lives at products/, not apps/products/, despite
    what the part spec's literal file list says.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "products"
