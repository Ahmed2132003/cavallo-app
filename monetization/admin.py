from datetime import timedelta
from types import SimpleNamespace

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from monetization.lifecycle import drop_profile_cache
from monetization.models import FeaturedSubscription, Plan, Subscription
from monetization.services import (
    activate_subscription,
    calculate_custom_price,
    deactivate_subscriptions,
    parse_gb,
)
from payments.storage_activation import activate_storage_subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "code",
        "name",
        "name_ar",
        "name_en",
        "duration_days",
        "price",
        "price_egp",
        "currency",
        "storage_bytes",
        "is_featured",
        "trial_days",
        "is_custom",
        "is_active",
        "sort_order",
        "updated_at",
    )
    list_filter = ("currency", "code", "is_active", "is_featured", "is_custom")
    search_fields = ("name", "name_ar", "name_en", "code")
    ordering = ("sort_order", "id")
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
        for attr in (
            "id",
            "starts_at",
            "expires_at",
            "is_active",
            "created_at",
            "updated_at",
        ):
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


class ManualActivationActionForm(helpers.ActionForm):
    """Extra fields beside the actions dropdown (read by activate_manually)."""

    # Deliberately unfiltered: the action itself validates the plan and says
    # why it refuses (a failing ActionForm only shows "No action selected").
    # Plain text on purpose: activate_manually() looks the plan up and
    # validates it itself, and says why it refuses.
    plan = forms.CharField(required=False, label="Plan id")
    gb = forms.CharField(required=False, label="GB (custom plan only)")


class SubscriptionAdminForm(forms.ModelForm):
    class Meta:
        model = Subscription
        fields = "__all__"

    def clean_status(self):
        status = self.cleaned_data["status"]
        if (
            self.instance.pk
            and status != self.instance.status
            and status in (Subscription.STATUS_ACTIVE, Subscription.STATUS_TRIALING)
        ):
            raise forms.ValidationError(
                "Activation goes through the payment/activation service, "
                "not a manual status edit."
            )
        return status


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """BE-02: storage-plan subscription state (no enforcement here)."""

    list_display = (
        "id",
        "business",
        "plan",
        "status",
        "started_at",
        "trial_ends_at",
        "current_period_end",
        "quota_bytes",
        "custom_gb",
        "purge_at",
        "pending_plan",
        "canceled_at",
    )
    list_filter = ("status", "plan")
    list_select_related = ("business", "plan", "pending_plan")
    raw_id_fields = ("business",)
    ordering = ("-id",)
    form = SubscriptionAdminForm
    action_form = ManualActivationActionForm
    actions = ("extend_trial_7d", "activate_manually")
    _EDITABLE = ("status",)

    def get_readonly_fields(self, request, obj=None):
        # Dates/quota/plan are changed only by the lifecycle services.
        return [
            f.name for f in self.model._meta.fields if f.name not in self._EDITABLE
        ]

    def has_add_permission(self, request):
        return False  # rows are created by the trial/purchase services

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data:
            if obj.status in (Subscription.STATUS_LOCKED, Subscription.STATUS_EXPIRED):
                obj.is_featured_effective = False
        super().save_model(request, obj, form, change)
        drop_profile_cache(obj.business_id)

    @admin.action(
        description="Extend trial by 7 days (trialing only)", permissions=["change"]
    )
    def extend_trial_7d(self, request, queryset):
        extended = 0
        now = timezone.now()
        for pk in list(queryset.values_list("pk", flat=True)):
            with transaction.atomic():
                sub = Subscription.objects.select_for_update().get(pk=pk)
                if sub.status != Subscription.STATUS_TRIALING:
                    continue
                base = max(sub.trial_ends_at or now, now)
                sub.trial_ends_at = base + timedelta(days=7)
                sub.save(update_fields=["trial_ends_at", "updated_at"])
                drop_profile_cache(sub.business_id)
            extended += 1
        self.message_user(
            request, f"Extended {extended} trial(s).", messages.SUCCESS
        )

    @admin.action(
        description="Activate manually (pick a paid plan above; custom needs GB)",
        permissions=["change"],
    )
    def activate_manually(self, request, queryset):
        """
        Offline payment received: grant a paid 30-day period of the chosen plan
        through the SAME service the payment webhook uses (so purge_at is
        cleared, quota/featured follow the plan, and the owner is notified).
        """
        raw = request.POST.get("plan", "")
        plan = Plan.objects.filter(pk=raw).first() if raw.isdigit() else None
        if plan is None:
            self.message_user(
                request, "Pick a plan in the action bar first.", messages.ERROR
            )
            return
        if plan.code == "free" or not plan.is_active:
            self.message_user(
                request, "Choose an active, paid plan (not Free).", messages.ERROR
            )
            return
        gb = None
        if plan.is_custom:
            try:
                gb = parse_gb(request.POST.get("gb", ""))
                calculate_custom_price(gb)  # also enforces the GB range
            except DRFValidationError:
                self.message_user(
                    request,
                    "A valid GB value is required for a custom plan.",
                    messages.ERROR,
                )
                return
        activated = 0
        for sub in queryset.select_related("business"):
            activate_storage_subscription(
                sub.business, plan, gb, SimpleNamespace(pk=None)
            )
            activated += 1
        self.message_user(
            request, f"Activated {activated} subscription(s).", messages.SUCCESS
        )
