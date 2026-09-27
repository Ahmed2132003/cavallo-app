"""
ChatConsumer (Part P-067 + P-068)

P-067: WebSocket connection + authorization skeleton — accept a
connection scoped to one conversation, verify the connecting user
(resolved onto scope["user"] by chat.middleware.JWTAuthMiddleware) is
actually a ConversationParticipant of that conversation, join a
Channels group for it, and leave the group cleanly on disconnect.

P-068 (this addition): the group-send handler. MessageSendView
(chat/views.py) persists a Message first, then — as an independent,
best-effort step whose failure cannot affect that view's already-sent
HTTP response — calls
    async_to_sync(channel_layer.group_send)(
        f"conversation_{conversation_id}",
        {"type": "chat.message", "message": <serialized message>},
    )
Channels' own dispatch convention maps the dot in "type" to a method
name with the dot replaced by an underscore, so "chat.message" calls
chat_message() below on every consumer instance currently joined to
that group's f"conversation_{conversation_id}" channel — including,
if connected, the sender's own other device/tab. This method's only
job is to forward that payload to its own WebSocket client as JSON
text; it does not touch the database and cannot itself affect
persistence, matching the "broadcast is a pure downstream effect of
an already-persisted message" contract this part establishes.

The participant check in connect() is this part's own concrete
application of the IDOR discipline established since Part P-026/P-058
(object-level authorization, not just "is this user logged in"): an
authenticated user with a perfectly valid token must still be REJECTED
if they are not a participant of the target conversation, or any
authenticated account could eavesdrop on any conversation by
guessing/enumerating conversation_id values.

Close codes used (the 4000-4999 range is reserved by the WebSocket spec
for application-defined codes, distinct from the 1000-2999 range used
by the protocol/browser itself):
    4001 — unauthenticated (missing/invalid/expired token)
    4003 — authenticated, but not a participant of this conversation
"""

import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from chat.models import ConversationParticipant


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        self.group_name = f"conversation_{self.conversation_id}"

        user = self.scope["user"]
        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        is_participant = await self._is_participant(user, self.conversation_id)
        if not is_participant:
            await self.close(code=4003)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        # group_name is always set (assigned at the top of connect(),
        # before any auth check), so this is safe to call even for a
        # connection that was closed in connect() before group_add()
        # ever ran — group_discard on a channel that was never a member
        # of the group is a harmless no-op on RedisChannelLayer.
        group_name = getattr(self, "group_name", None)
        if group_name:
            await self.channel_layer.group_discard(group_name, self.channel_name)

    async def chat_message(self, event):
        """
        Group-send handler (Part P-068) for events dispatched by
        MessageSendView.post() via
        channel_layer.group_send(group_name, {"type": "chat.message", ...}).
        Simply forwards the already-serialized message data straight
        to this connected WebSocket client as JSON text. No DB access,
        no side effects beyond the send — the message this represents
        was already persisted, independently, before this handler ever
        runs.
        """
        await self.send(text_data=json.dumps(event["message"]))

    @database_sync_to_async
    def _is_participant(self, user, conversation_id):
        """
        Object-level authorization check — is `user` actually a
        ConversationParticipant of `conversation_id`? Wrapped in
        database_sync_to_async since this is a synchronous ORM call
        made from inside an async consumer.
        """
        return ConversationParticipant.objects.filter(
            conversation_id=conversation_id, user=user
        ).exists()
