from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from accounts.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """
    Django Admin registration for the custom User model (Part P-016).

    Extends Django's own UserAdmin (rather than starting from
    admin.ModelAdmin) so username/password-change/permissions screens
    keep working exactly as they do for the stock User model — only the
    three new fields (account_type, is_moderator, is_business_verified)
    are added on top, in both the list view and the edit/add forms.
    """

    list_display = DjangoUserAdmin.list_display + (
        "account_type",
        "is_moderator",
        "is_business_verified",
    )
    list_filter = DjangoUserAdmin.list_filter + (
        "account_type",
        "is_moderator",
        "is_business_verified",
    )
    fieldsets = DjangoUserAdmin.fieldsets + (
        (
            "Account type & roles (Part P-016)",
            {
                "fields": (
                    "account_type",
                    "is_moderator",
                    "is_business_verified",
                )
            },
        ),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        (
            "Account type & roles (Part P-016)",
            {
                "fields": (
                    "account_type",
                    "is_moderator",
                    "is_business_verified",
                )
            },
        ),
    )
