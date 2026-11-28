"""BE-03: permission for the merchant-only subscription endpoints."""

from rest_framework.permissions import BasePermission

from monetization.services import is_locked


class HasActiveSubscription(BasePermission):
    """
    BE-06 Subscription Gate. Blocks a business account whose subscription
    is locked/expired (per services.is_locked) with 403 code
    "subscription_required".

    Passes (never blocks): anonymous (auth classes decide), staff/superuser,
    any non-business account (customer, moderator), a business user with no
    BusinessProfile yet (onboarding), and a business with no Subscription row.

    Always list it AFTER IsAuthenticated. Do NOT put it on auth, payments,
    plans, /subscription/me, /me or any public read.
    """

    message = "Your subscription has expired. Renew to continue."
    code = "subscription_required"

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return True
        if user.is_staff or user.is_superuser:
            return True
        if getattr(user, "account_type", None) != "business":
            return True
        business = getattr(user, "business_profile", None)
        if business is None:
            return True
        return not is_locked(business)


class IsBusinessAccount(BasePermission):
    """
    Authenticated user whose account_type is "business". Customers and
    moderators are excluded from plans entirely. Anonymous callers get 401
    (JWT authenticate header), signed-in non-business callers get 403.
    """

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, "account_type", None) == "business"
        )