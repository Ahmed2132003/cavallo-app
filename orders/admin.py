"""
Part BI-01 — read-only admin. Orders are created and moved through the API
only (transition rules, events, ownership); the admin never edits them.
ConversationProductInterest is registered here (not in chat/admin.py,
which is not part of the review bundle).
"""

from django.contrib import admin

from chat.models import ConversationProductInterest

from .models import Order, OrderEvent


class _ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class OrderEventInline(admin.TabularInline):
    model = OrderEvent
    extra = 0
    can_delete = False
    fields = (
        "created_at",
        "event_type",
        "from_status",
        "to_status",
        "is_internal",
        "actor",
        "note",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(_ReadOnlyAdmin):
    list_display = (
        "id",
        "business",
        "customer",
        "product",
        "quantity",
        "total_amount",
        "currency",
        "status",
        "created_at",
    )
    list_select_related = ("business", "customer", "product")
    list_filter = ("status", "currency")
    search_fields = (
        "business__business_name",
        "customer__email",
        "customer__username",
    )
    inlines = [OrderEventInline]


@admin.register(OrderEvent)
class OrderEventAdmin(_ReadOnlyAdmin):
    list_display = (
        "id",
        "order",
        "event_type",
        "from_status",
        "to_status",
        "is_internal",
        "created_at",
    )
    list_filter = ("event_type", "is_internal")


@admin.register(ConversationProductInterest)
class ConversationProductInterestAdmin(_ReadOnlyAdmin):
    list_display = (
        "id",
        "business",
        "product",
        "conversation",
        "customer",
        "source",
        "first_opened_at",
        "last_opened_at",
    )
    list_select_related = ("business", "product", "customer")
    list_filter = ("source",)
