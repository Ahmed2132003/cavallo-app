"""
Django Admin registration for the notifications app (Part P-078).
"""

from django.contrib import admin

from notifications.models import Notification, NotificationPreference


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "recipient",
        "notification_type",
        "deep_link_type",
        "target_id",
        "is_read",
        "created_at",
    )
    list_filter = ("notification_type", "deep_link_type", "is_read")
    search_fields = ("recipient__username", "recipient__email", "title", "body")
    raw_id_fields = ("recipient",)
    list_select_related = ("recipient",)
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "chat_notifications_enabled",
        "moderation_notifications_enabled",
        "social_notifications_enabled",
    )
    list_filter = (
        "chat_notifications_enabled",
        "moderation_notifications_enabled",
        "social_notifications_enabled",
    )
    search_fields = ("user__username", "user__email")
    raw_id_fields = ("user",)
    list_select_related = ("user",)
    readonly_fields = ("created_at", "updated_at")
