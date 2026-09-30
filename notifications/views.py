"""
Views for Part P-082 - notification-center REST API.

GET   /api/v1/notifications/                 own notifications, cursor-paginated
PATCH /api/v1/notifications/{id}/read/       mark ONE OWN notification read
GET   /api/v1/notifications/preferences/     own preference toggles
PATCH /api/v1/notifications/preferences/     update own preference toggles

IDOR discipline (architecture Section 5 rule 10, same as P-026 onward):

* The list and the mark-read lookup are both scoped with
  ``recipient=request.user``. A notification that belongs to somebody
  else is indistinguishable from one that does not exist: both answer
  404 (explicit DRF NotFound, so the standard {"error": {...}} envelope
  applies). Nothing about another user's notification ever leaks.
* The preferences endpoints have no id in the URL at all. They always
  operate on request.user's own NotificationPreference row.

Nothing here creates a Notification or sends a push. Only
notifications/tasks.py may do that (P-079 architecture rule).
"""

from rest_framework import generics
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardCursorPagination
from notifications.models import Notification, NotificationPreference
from notifications.serializers import (
    NotificationPreferenceSerializer,
    NotificationSerializer,
)


class NotificationListView(generics.ListAPIView):
    """GET /api/v1/notifications/ - the caller's own notifications, newest first."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        # StandardCursorPagination orders by "-created_at" itself, which
        # matches the notif_recipient_created_idx index (recipient,
        # -created_at).
        return Notification.objects.filter(recipient=self.request.user)


class NotificationMarkReadView(APIView):
    """
    PATCH /api/v1/notifications/{id}/read/ - mark one own notification read.

    Idempotent: marking an already-read notification is a no-op that
    still answers 200 with the notification. The request body is ignored
    entirely (the endpoint's meaning is fixed: "this is now read"), so a
    client cannot un-read a notification or change any other field.
    """

    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        notification = Notification.objects.filter(
            pk=pk, recipient=request.user
        ).first()
        if notification is None:
            raise NotFound("Notification not found.")

        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=["is_read", "updated_at"])

        return Response(NotificationSerializer(notification).data)


def _get_own_preferences(user):
    # get_or_create: the post_save signal and the 0002 backfill make a
    # missing row unexpected, but a missing row must never become a 500.
    # It is created with the model defaults (everything enabled), which
    # is also how P-079 treats a missing row.
    preferences, _created = NotificationPreference.objects.get_or_create(user=user)
    return preferences


class NotificationPreferenceView(APIView):
    """
    GET   /api/v1/notifications/preferences/ - the caller's three toggles.
    PATCH /api/v1/notifications/preferences/ - partial update of them.

    No id in the URL: the row is always request.user's own.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        preferences = _get_own_preferences(request.user)
        return Response(NotificationPreferenceSerializer(preferences).data)

    def patch(self, request):
        preferences = _get_own_preferences(request.user)
        serializer = NotificationPreferenceSerializer(
            preferences, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
