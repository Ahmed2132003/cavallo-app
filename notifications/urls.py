"""
URL routes for Part P-082's notification-center endpoints.

Mounted under /api/v1/notifications/ by config/urls.py.
"""

from django.urls import path

from notifications.views import (
    NotificationListView,
    NotificationMarkReadView,
    NotificationPreferenceView,
)

app_name = "notifications"

urlpatterns = [
    path("", NotificationListView.as_view(), name="list"),
    path("preferences/", NotificationPreferenceView.as_view(), name="preferences"),
    path("<int:pk>/read/", NotificationMarkReadView.as_view(), name="mark-read"),
]
