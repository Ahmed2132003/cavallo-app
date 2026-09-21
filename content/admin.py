from django.contrib import admin

from content.models import Post, Reel


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("id", "business", "status", "created_at", "is_deleted")
    list_filter = ("status", "is_deleted")
    autocomplete_fields = ("business",)
    readonly_fields = ("status",)


@admin.register(Reel)
class ReelAdmin(admin.ModelAdmin):
    # processing_status is included here (read-only) so an admin can see
    # a stuck/failed transcode without needing shell access — it is
    # never editable here, only ever set by content.tasks.transcode_reel().
    list_display = (
        "id",
        "business",
        "status",
        "processing_status",
        "created_at",
        "is_deleted",
    )
    list_filter = ("status", "processing_status", "is_deleted")
    autocomplete_fields = ("business",)
    readonly_fields = ("status", "processing_status", "duration_seconds")