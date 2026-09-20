from django.contrib import admin

from content.models import Post


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("id", "business", "status", "created_at", "is_deleted")
    list_filter = ("status", "is_deleted")
    autocomplete_fields = ("business",)
    readonly_fields = ("status",)