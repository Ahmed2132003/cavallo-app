from django.contrib import admin

from stories.models import Story


@admin.register(Story)
class StoryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "business",
        "status",
        "published_at",
        "expires_at",
        "created_at",
        "is_deleted",
    )
    list_filter = ("status", "is_deleted")
    autocomplete_fields = ("business",)
    readonly_fields = ("status", "published_at", "expires_at")
