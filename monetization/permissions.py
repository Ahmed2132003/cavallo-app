"""BE-03: permission for the merchant-only subscription endpoints."""

from rest_framework.permissions import BasePermission


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