from django.contrib import admin
from django.utils import timezone

from reports.models import Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    """Admin visibility for reports (there is no Flutter review screen in MVP).

    Reports are created only through the API, so adding is disabled. Only
    ``status`` stays editable; everything else is read-only.
    """

    list_display = (
        "id",
        "reporter",
        "content_type",
        "object_id",
        "reason",
        "status",
        "created_at",
    )
    list_filter = ("status", "reason", "content_type")
    search_fields = ("reporter__username", "reporter__email", "details")
    list_select_related = ("reporter", "content_type")
    ordering = ("-created_at",)
    readonly_fields = (
        "reporter",
        "content_type",
        "object_id",
        "reason",
        "details",
        "created_at",
        "updated_at",
    )
    actions = ["mark_reviewed"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Mark selected reports as reviewed")
    def mark_reviewed(self, request, queryset):
        updated = queryset.filter(status=Report.Status.PENDING).update(
            status=Report.Status.REVIEWED,
            updated_at=timezone.now(),
        )
        self.message_user(request, f"{updated} report(s) marked as reviewed.")
