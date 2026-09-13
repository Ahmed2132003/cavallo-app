from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """
    Custom user model + role fields (Part P-016).

    First part of Phase 3. Every other app in the system depends on the
    User model defined here — see accounts/models.py's module docstring
    for the field-to-role mapping (account_type, is_staff, is_superuser,
    is_moderator, is_business_verified) that P-019's permission-flag
    system, and every future admin-action part, build on top of.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
