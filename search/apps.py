"""
Part P-063 - search app (Phase 11, ADR-003).

Logical home for Postgres full-text search infrastructure over
Product and BusinessProfile. This app deliberately owns no models of
its own: search_vector lives directly on Product/BusinessProfile
(added in this same part), and the actual query endpoint is Part
P-064's job. The app exists purely so P-064 has a conventional home
to land in, matching P-036 (moderation) and P-059 (feed)'s precedent
of model-light/model-less apps for cross-cutting concerns.
"""

from django.apps import AppConfig


class SearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "search"

    def ready(self):
        # Registers the post_save signal handlers that keep
        # Product.search_vector / BusinessProfile.search_vector in
        # sync on every create/update. Imported here (not at module
        # load time) per Django's standard convention for wiring
        # signal receivers, so app-registry/model loading order is
        # never a problem - matches categories/apps.py's ready()
        # precedent (P-025).
        import search.signals  # noqa: F401