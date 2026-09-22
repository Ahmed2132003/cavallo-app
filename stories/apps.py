from django.apps import AppConfig


class StoriesConfig(AppConfig):
    """
    Part P-046. Follows the project-wide top-level-app convention
    established in P-011/P-012/P-013/P-016/P-024/P-025/P-036/P-041
    (no ``apps/`` package) — this app lives at ``stories/``, not
    ``apps/stories/``, matching the Part spec's own "Files Expected"
    list adapted to the real project convention (see this part's
    PROJECT_PROGRESS.md entry).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "stories"
