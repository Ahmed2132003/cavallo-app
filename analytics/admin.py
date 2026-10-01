from django.contrib import admin

from analytics.models import BusinessDailyStats


@admin.register(BusinessDailyStats)
class BusinessDailyStatsAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "business",
        "date",
        "new_followers",
        "total_likes_received",
        "total_comments_received",
        "total_story_views",
        "updated_at",
    )
    list_filter = ("date",)
    date_hierarchy = "date"
    autocomplete_fields = ("business",)
    # The counters are produced by the daily rollup job; editing them by
    # hand would silently diverge from the raw tables.
    readonly_fields = (
        "new_followers",
        "total_likes_received",
        "total_comments_received",
        "total_story_views",
        "created_at",
        "updated_at",
    )