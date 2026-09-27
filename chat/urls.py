from django.urls import path

from chat.views import ConversationStartView

app_name = "chat"

urlpatterns = [
    path("start/", ConversationStartView.as_view(), name="conversation-start"),
]
