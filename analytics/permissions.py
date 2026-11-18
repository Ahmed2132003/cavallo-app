from rest_framework.permissions import BasePermission


class CanRecordViewEvent(BasePermission):
    """
    Part BI-02. WHO MAY RECORD a view: everyone, logged in or anonymous.
    Deliberately separate from IsAnalyticsBusinessOwner (WHO MAY READ
    analytics): being allowed to record a view says nothing about being
    allowed to read any number.
    """

    def has_permission(self, request, view):
        return True


class IsAnalyticsBusinessOwner(BasePermission):
    """
    Part BI-02. Read access to a business's analytics: authenticated AND the
    owner of that exact business. has_object_permission receives the
    BusinessProfile resolved from the URL id (never from query/body).
    """

    message = "You do not have permission to view this business's analytics."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        return obj.user_id == request.user.id
