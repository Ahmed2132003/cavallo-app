from django.urls import path

from chat.views import ConversationStartView, MessageSendView

app_name = "chat"

urlpatterns = [
    path("start/", ConversationStartView.as_view(), name="conversation-start"),
    path(
        "<int:conversation_id>/messages/",
        MessageSendView.as_view(),
        name="conversation-messages",
    ),
]
