from django.contrib import admin, messages

from monetization.models import FeaturedSubscription, Plan
from monetization.services import activate_subscription, deactivate_subscriptions


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "duration_days", "price", "currency", "updated_at")
    list_filter = ("currency",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(FeaturedSubscription)
class FeaturedSubscriptionAdmin(admin.ModelAdmin):
    """
    Manual-activation path (Architecture Section 4, "manual activation
    acceptable initially"). Fully payment-independent.

    Every way of making a subscription active here goes through
    monetization.services.activate_subscription():
      - the Add form (business + plan only) calls it from save_model();
      - the "Re-activate selected" action calls it per business/plan.
    The active flag, starts_at and expires_at are never editable by hand.
    """

    list_display = ("id", "business", "plan", "starts_at", "expires_at", "is_active")
    list_filter = ("is_active", "plan")
    list_select_related = ("business", "plan")
    ordering = ("-id",)
    autocomplete_fields = ("business",)
    actions = ("reactivate_selected", "deactivate_selected")

    _CHANGE_FIELDS = (
        "business",
        "plan",
        "starts_at",
        "expires_at",
        "is_active",
        "created_at",
        "updated_at",
    )

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("business", "plan")
        return self._CHANGE_FIELDS

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return self._CHANGE_FIELDS

    def save_model(self, request, obj, form, change):
        if change:
            # Every field is read-only on the change page.
            super().save_model(request, obj, form, change)
            return
        created = activate_subscription(obj.business, obj.plan)
        # Hand the freshly created row's state back to the admin's
        # in-memory instance so its redirect / log entry use the real row.
        for attr in ("id", "starts_at", "expires_at", "is_active", "created_at", "updated_at"):
            setattr(obj, attr, getattr(created, attr))
        obj._state.adding = False
        obj._state.db = created._state.db

    @admin.action(description="Re-activate selected (grants a fresh period)")
    def reactivate_selected(self, request, queryset):
        seen = set()
        activated = 0
        for sub in queryset.select_related("business", "plan").order_by("-id"):
            key = (sub.business_id, sub.plan_id)
            if key in seen:
                continue
            seen.add(key)
            activate_subscription(sub.business, sub.plan)
            activated += 1
        self.message_user(
            request,
            f"Activated {activated} subscription(s).",
            messages.SUCCESS,
        )

    @admin.action(description="Deactivate selected")
    def deactivate_selected(self, request, queryset):
        # P-087: goes through the service so BusinessProfile.is_featured
        # is cleared in the same transaction as the subscription flag.
        updated = deactivate_subscriptions(queryset)
        self.message_user(
            request,
            f"Deactivated {updated} subscription(s).",
            messages.SUCCESS,
        )