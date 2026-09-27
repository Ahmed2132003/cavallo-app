"""
WebSocket URL routing for the chat app (Part P-067).

This is Channels' own URLRouter table — kept separate from
config/urls.py, which only holds HTTP path() patterns. config/asgi.py
imports websocket_urlpatterns from here and wraps it in
chat.middleware.JWTAuthMiddlewareStack.

URL convention (locked contract — see chat/consumers.py and
chat/middleware.py docstrings; Part P-073's Flutter WebSocket client
must construct this exact path + query param):

    ws://<host>/ws/conversations/<conversation_id>/?token=<access_token>
"""

from django.urls import path

from chat.consumers import ChatConsumer

websocket_urlpatterns = [
    path(
        "ws/conversations/<int:conversation_id>/",
        ChatConsumer.as_asgi(),
    ),
]