"""
Part P-109 - ratings app (Phase 11, out-of-sequence ID - see this
part's own docstring/handoff for why).

Closes a real gap: Search & Filters (P-064) referenced a rating-based
filter (Section 9-B's backlog) with no underlying model anywhere in
the system. This app owns Rating and the two denormalized columns it
maintains on BusinessProfile (average_rating, ratings_count).
"""

from django.apps import AppConfig


class RatingsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ratings"
