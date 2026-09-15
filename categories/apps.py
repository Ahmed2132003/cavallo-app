from django.apps import AppConfig


class CategoriesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "categories"

    def ready(self):
        # Registers the post_save/post_delete signal handlers that
        # invalidate the "categories:tree" cache key. Imported here
        # (not at module load time) per Django's standard convention
        # for wiring signal receivers, so app-registry/model loading
        # order is never a problem.
        import categories.signals  # noqa: F401
