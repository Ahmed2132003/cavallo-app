"""
ChatConsumer (Part P-067) — WebSocket connection + authorization
skeleton ONLY.

Scope for this part: accept a WebSocket connection scoped to one
conversation, verify the connecting user (already resolved onto
scope["user"] by chat.middleware.JWTAuthMiddleware) is actually a
ConversationParticipant of that conversation, join a Channels group
for it, and leave the group cleanly on disconnect.

Deliberately OUT of scope here (see PROJECT_IMPLEMENTATION_MASTER_PLAN,
Part P-067 "Out of Scope"): no receive()/group-send message-broadcast
logic at all — that begins in Part P-068 (persistence-first send).
This consumer can join/leave a group and nothing more; receive() is
intentionally left un-overridden (AsyncWebsocketConsumer's own default
is a no-op), so any inbound WebSocket frame is silently ignored for now
rather than half-implemented.

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
