from django.contrib import admin

from .models import ModerationQueue


@admin.register(ModerationQueue)
class ModerationQueueAdmin(admin.ModelAdmin):
    """
    Read-only list view for now — this part is scoped to the queue
    model + mixin only. Real moderator actions (approve/reject from
    Admin or a dedicated API) land in P-038; wiring them here early
    would be building outside this part's explicit scope.
    """

    list_display = (
        "id",
        "content_type",
        "object_id",
        "status",
        "priority",
        "created_at",
    )
    list_filter = ("status", "priority", "content_type")
    search_fields = ("object_id",)
    readonly_fields = (
        "content_type",
        "object_id",
        "status",
        "priority",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
