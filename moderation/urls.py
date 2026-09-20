"""
URL routes for the moderator queue API (Part P-038).

Mounted at /api/v1/moderation/ by config/urls.py.
"""

from django.urls import path

from moderation.views import ApproveView, ModerationQueueListView, RejectView

urlpatterns = [
    path("queue/", ModerationQueueListView.as_view(), name="moderation-queue-list"),
    path(
        "queue/<int:pk>/approve/",
        ApproveView.as_view(),
        name="moderation-queue-approve",
    ),
    path(
        "queue/<int:pk>/reject/",
        RejectView.as_view(),
        name="moderation-queue-reject",
    ),
]
