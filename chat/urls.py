from django.urls import path

from chat.views import (
    ConversationStartView,
    UserPresenceView,
    message_collection_view,
)

app_name = "chat"

urlpatterns = [
    path("start/", ConversationStartView.as_view(), name="conversation-start"),
    # Part P-072: هذا المسار بقى بيوجّه لـ message_collection_view
    # (dispatcher)، مش MessageSendView.as_view() مباشرة — عشان GET
    # (fetch-since, P-072) وPOST (send, P-068) يشتغلوا على نفس الـ URL
    # بالظبط زي ما الـ spec بيحدد. نفس الـ pattern المستخدم في
    # social/comment_urls.py's comment_collection_view (Part P-055).
    path(
        "<int:conversation_id>/messages/",
        message_collection_view,
        name="conversation-messages",
    ),
    # Part P-070 STEP 2 -- see chat/views.py's UserPresenceView
    # docstring for why this lives under the existing
    # /api/v1/conversations/ prefix instead of a new /api/v1/users/
    # prefix the spec named but that doesn't exist in this project.
    path(
        "users/<int:user_id>/presence/",
        UserPresenceView.as_view(),
        name="user-presence",
    ),
]