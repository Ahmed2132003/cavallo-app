from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    """
    Part P-089. Top-level app (``payments/``, not ``apps/payments/``),
    per the project-wide convention in force since P-011.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "payments"
