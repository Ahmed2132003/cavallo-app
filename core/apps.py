from django.apps import AppConfig


class CoreConfig(AppConfig):
    """
    Shared infrastructure app (Part P-011).

    Holds abstract base model mixins (TimestampedModel, SoftDeleteModel),
    the project-wide cursor pagination class, and the module location for
    the permission-flag system landing in P-019. Every future content app
    depends on this app; this app depends on nothing project-specific.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
