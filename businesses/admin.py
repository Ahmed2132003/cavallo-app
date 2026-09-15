from django.contrib import admin

from .models import BusinessProfile, CustomerProfile


@admin.register(BusinessProfile)
class BusinessProfileAdmin(admin.ModelAdmin):
    list_display = (
        "business_name",
        "user",
        "business_type",
        "country",
        "city",
        "verified_status",
        "created_at",
    )
    list_filter = ("business_type", "country")
    search_fields = ("business_name", "user__email")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(boolean=True, description="Verified")
    def verified_status(self, obj):
        # Read-through to User.is_business_verified — see models.py.
        # Verification itself is toggled on the User admin page
        # (P-016's "Verify" action), not here.
        return obj.is_verified


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "user", "country", "city", "created_at")
    search_fields = ("display_name", "user__email")
    readonly_fields = ("created_at", "updated_at")