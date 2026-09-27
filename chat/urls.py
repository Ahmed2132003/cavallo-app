from django.urls import path

from chat.views import ConversationStartView, MessageSendView, UserPresenceView

app_name = "chat"

urlpatterns = [
    path("start/", ConversationStartView.as_view(), name="conversation-start"),
    path(
        "<int:conversation_id>/messages/",
        MessageSendView.as_view(),
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
