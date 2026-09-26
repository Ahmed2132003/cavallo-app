from django.contrib import admin

from ratings.models import Rating


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin):
    """Admin visibility for ratings (there is no Flutter "all reviews"
    browsing screen in MVP, per this part's own Out of Scope note).

    Ratings are created/updated only through the API (the upsert
    contract matters - customer/business/score must never drift out
    of sync with each other via manual Admin edits), so adding is
    disabled and the identifying fields are read-only. Only
    ``review_text`` stays editable, for moderator cleanup of an
    abusive review flagged via the generic Report mechanism (P-057/
    P-058), reused rather than duplicated for reviews.
    """

    list_display = ("id", "customer", "business", "score", "created_at")
    list_filter = ("score",)
    search_fields = (
        "customer__username",
        "customer__email",
        "business__business_name",
        "review_text",
    )
    list_select_related = ("customer", "business")
    ordering = ("-created_at",)
    readonly_fields = ("customer", "business", "score", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False
