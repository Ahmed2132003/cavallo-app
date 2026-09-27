"""
ChatConsumer (Part P-067 + P-068 + P-069 + P-070)

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

P-069 (this addition): the recipient-side delivery/read acknowledgment
flow. The recipient's connected client sends "mark_delivered" or
"mark_read" WebSocket events (each carrying {"message_id": <id>}); this
consumer applies the corresponding Message.status transition through
an explicit, forward-only ALLOWED_TRANSITIONS guard, never by blindly
overwriting status with whatever the client claims, and, only when a
transition is actually applied, broadcasts a "status_update" event
({"message_id": ..., "status": <new_status>}) to the conversation's
group so the original sender's connected client learns the new status
in real time. This is WebSocket-only by design (matching the "acks
happen while the recipient is actively connected" framing established
in P-069's spec) -- there is no REST equivalent.

The regression guard: a mark_delivered ack that arrives after a
message is already 'read' must be a silent no-op, not a regression,
since a client racing multiple acks (e.g. a delivered ack queued
before the read ack, arriving after it) is normal, not malicious.
ALLOWED_TRANSITIONS encodes the one-way progression
(sent -> delivered -> read) directly rather than trusting client-
supplied ordering; an already-applied target status (current == target)
is likewise treated as a no-op, not an error, so no incorrect
status_update is ever broadcast claiming a change that didn't happen.

P-070 (this addition): Redis-backed online/offline presence, reusing
Django's existing cache framework (Part P-014, django_redis) rather
than a separate raw-Redis client — per this part's own Architecture
Rules, a raw client would be a second, inconsistent way of talking to
the same Redis instance the cache framework already manages.

    - connect() sets `online:{user.id}` in the cache (via
      `_set_online()`) once the connection is actually accepted (i.e.
      after the same auth + participant checks P-067 already enforces
      -- a rejected connection never marks anyone online).
    - The client is expected to send a periodic WebSocket event
      `{"type": "heartbeat"}` (LOCKED CONTRACT for Part P-073's Flutter
      connection manager -- no other fields required) at an interval
      comfortably under PRESENCE_TTL_SECONDS (60s); every 30s is the
      reference interval. receive() dispatches this to the same
      `_set_online()` call, which re-sets the same key/timeout and so
      naturally extends it -- there is no separate "refresh" API,
      matching the spec's own suggestion.
    - disconnect() explicitly deletes the key (`_clear_online()`)
      rather than waiting for the TTL to lapse, so an explicit
      disconnect reads as "offline" immediately, not up to 60s later.
      This only runs for a user who was actually authenticated
      (scope["user"].is_authenticated) -- a connection rejected before
      ever being accepted never had a key to clear, and deleting a
      cache key that was never set is a harmless no-op regardless.

    Known limitation (out of this part's stated scope, flagging rather
    than silently handling): this key format assumes at most one
    "online-ness" signal per user, not per-connection. If the same
    user opens a second WebSocket connection (another device/tab) and
    then closes the FIRST one, that disconnect's _clear_online() will
    mark the user offline even though their second connection is still
    live. The spec doesn't call out multi-connection reference
    counting as an MVP requirement, so this isn't handled here -- worth
    a deliberate decision (e.g. a per-connection sub-key, or a
    reference count) if/when multi-device simultaneous connections
    become a real scenario.

    The GET /api/v1/users/{id}/presence/ REST endpoint that reads this
    same key is Part P-070's other half -- not implemented in this
    file (see chat/views.py); presence_cache_key() below is exported
    so that view can build the exact same key without duplicating the
    "online:{id}" string literal in two places.
"""

import json

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.cache import cache

from chat.models import ConversationParticipant, Message

# Forward-only transition table (Part P-069). A status only ever moves
# rightward through sent -> delivered -> read; 'read' has no further
# allowed target, so it maps to an empty set.
ALLOWED_TRANSITIONS = {
    Message.Status.SENT: {Message.Status.DELIVERED, Message.Status.READ},
    Message.Status.DELIVERED: {Message.Status.READ},
    Message.Status.READ: set(),
}

# Incoming WebSocket event "type" -> the Message.status it acknowledges.
# Locked contract -- chat/consumers.py's docstring above and P-069's
# Handoff Notes require P-073/P-074's Flutter client to send these
# exact event names and field names.
_ACK_EVENT_TARGET_STATUS = {
    "mark_delivered": Message.Status.DELIVERED,
    "mark_read": Message.Status.READ,
}

# Part P-070. Seconds a presence key survives without a refreshing
# heartbeat -- matches the spec's own "short TTL (e.g. 60 seconds)".
# A module-level constant (rather than a literal inline) so tests can
# patch it to a much smaller value instead of sleeping 60+ real
# seconds to prove the heartbeat genuinely extends the window.
PRESENCE_TTL_SECONDS = 60


def presence_cache_key(user_id):
    """
    Cache key for a user's online/offline presence flag (Part P-070).

    Single source of truth for the "online:{id}" format so chat/views.py's
    presence-check endpoint (this part's other half) and this consumer
    can never drift out of sync on the key shape.
    """
    return f"online:{user_id}"


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
        await self._set_online()

    async def disconnect(self, close_code):
        # group_name is always set (assigned at the top of connect(),
        # before any auth check), so this is safe to call even for a
        # connection that was closed in connect() before group_add()
        # ever ran — group_discard on a channel that was never a member
        # of the group is a harmless no-op on RedisChannelLayer.
        group_name = getattr(self, "group_name", None)
        if group_name:
            await self.channel_layer.group_discard(group_name, self.channel_name)

        # Part P-070: an explicit disconnect should read as "offline"
        # immediately, not up to PRESENCE_TTL_SECONDS later. Only clear
        # for a user who was actually authenticated -- a connection
        # rejected in connect() before ever being accepted never set a
        # presence key in the first place (clearing it anyway would be
        # a harmless no-op, but this guard makes the intent explicit).
        user = self.scope.get("user")
        if user is not None and user.is_authenticated:
            await self._clear_online()

    async def receive(self, text_data):
        """
        Parses one incoming WebSocket text frame as JSON and dispatches
        it to the right handler.

        Part P-070: a `{"type": "heartbeat"}` frame refreshes this
        connection's presence TTL and is handled first, before the
        P-069 ack dispatch below, since it isn't part of that event
        family and carries no "message_id".

        Part P-069: "mark_delivered" / "mark_read" acknowledgment
        events.

        Any frame that is not valid JSON, not a JSON object, or whose
        "type" doesn't match anything recognized above is silently
        ignored -- this consumer has no other inbound event types
        defined yet, and a malformed or unrecognized frame is not this
        part's concern to report on (no error is sent back to the
        client).
        """
        try:
            event = json.loads(text_data)
        except (TypeError, ValueError):
            return

        if not isinstance(event, dict):
            return

        event_type = event.get("type")

        if event_type == "heartbeat":
            await self._set_online()
            return

        if event_type == "heartbeat":
            await self._set_online()
            return

        if event_type == "typing":
            await self._handle_typing(event)
            return

        target_status = _ACK_EVENT_TARGET_STATUS.get(event_type)
        target_status = _ACK_EVENT_TARGET_STATUS.get(event_type)
        if target_status is None:
            return

        message_id = event.get("message_id")
        if message_id is None:
            return

        new_status = await self._apply_status_transition(message_id, target_status)
        if new_status is None:
            # No-op: message not found in this conversation, transition
            # already applied (idempotent), or a regressive/invalid
            # transition per ALLOWED_TRANSITIONS -- nothing to broadcast.
            return

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "status.update",
                "message_id": message_id,
                "status": new_status,
            },
        )

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

    async def status_update(self, event):
        """
        Group-send handler (Part P-069) for "status.update" events
        dispatched by receive() above via channel_layer.group_send() --
        Channels' own dispatch convention maps the dot in "type" to a
        method name with the dot replaced by an underscore, exactly as
        chat_message() above does for "chat.message". Forwards
        {"message_id": ..., "status": ...} straight to this connected
        WebSocket client as JSON text; in this MVP's 1:1-only scope this
        reaches both participants (including, harmlessly, the client
        that sent the ack itself), so no special sender-only targeting
        is needed.
        """
        await self.send(
            text_data=json.dumps(
                {"message_id": event["message_id"], "status": event["status"]}
            )
        )

    async def _handle_typing(self, event):
        """
        Part P-071. Handles an incoming `{"type": "typing", "is_typing":
        true|false}` frame. `is_typing` must be a real bool -- anything
        else (missing, a string, a number) is silently ignored, the same
        "malformed frame -> no-op" contract receive() already applies to
        every other event type in this consumer.

        Broadcasts a "typing.indicator" group-send event carrying this
        connection's own self.channel_name alongside is_typing, so every
        receiving consumer in the group (typing_indicator() below) can
        tell whether IT is the connection that sent this typing event and
        skip forwarding it back to that same client -- the sender must
        never receive their own typing broadcast.

        No database access of any kind happens here, and none ever will:
        per this part's Architecture Rules, typing state is WS-only and
        is never persisted in any form (no model write, no cache entry,
        no Celery task) -- there is no legitimate reason to retain a
        signal that only matters for its instantaneous real-time
        delivery.
        """
        is_typing = event.get("is_typing")
        if not isinstance(is_typing, bool):
            return

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "typing.indicator",
                "is_typing": is_typing,
                "sender_channel_name": self.channel_name,
            },
        )

    async def typing_indicator(self, event):
        """
        Group-send handler (Part P-071) for "typing.indicator" events
        dispatched by _handle_typing() above -- Channels' own dispatch
        convention maps the dot in "type" to a method name with the dot
        replaced by an underscore, exactly as chat_message() and
        status_update() above do for their own event types.

        Self-exclusion: if event["sender_channel_name"] matches this
        consumer's own self.channel_name, this IS the connection that
        sent the typing event, so it returns without calling self.send()
        -- the sender does not receive their own typing broadcast back.
        Every other consumer in the group forwards {"is_typing": ...} to
        its own connected client as JSON text. No DB access, no side
        effects beyond the send.
        """
        if event.get("sender_channel_name") == self.channel_name:
            return

        await self.send(text_data=json.dumps({"is_typing": event["is_typing"]}))

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

    async def _set_online(self):
        """
        Part P-070. Sets (or, called again from a heartbeat, re-sets --
        which is exactly how a TTL is naturally extended) this
        connection's user as online, timed out after
        PRESENCE_TTL_SECONDS. Wrapped in sync_to_async since Django's
        cache API (backed here by django_redis, per Part P-014) is a
        blocking synchronous call; thread_sensitive=False since this
        touches only the Redis cache client, not the ORM, so it doesn't
        need to be serialized onto Channels' single "database" thread
        the way database_sync_to_async calls are.
        """
        user = self.scope["user"]
        await sync_to_async(cache.set, thread_sensitive=False)(
            presence_cache_key(user.id), True, PRESENCE_TTL_SECONDS
        )

    async def _clear_online(self):
        """
        Part P-070. Explicitly deletes this connection's user's
        presence key -- called from disconnect() so an explicit
        disconnect reads as offline immediately rather than waiting up
        to PRESENCE_TTL_SECONDS for the key to lapse on its own.
        """
        user = self.scope["user"]
        await sync_to_async(cache.delete, thread_sensitive=False)(
            presence_cache_key(user.id)
        )

    @database_sync_to_async
    def _apply_status_transition(self, message_id, target_status):
        """
        Part P-069's forward-only status-transition guard.

        Looks up the Message scoped to *this* consumer's conversation
        (self.conversation_id) -- not just by message_id alone -- so an
        ack can never touch a message belonging to a different
        conversation the connected user happens to know the id of; the
        connect()-time participant check only proves membership of
        *this* conversation, so this scoping is what makes that
        authorization actually mean something for every subsequent ack.

        Returns the new status string once a transition is genuinely
        applied and saved, or None for every no-op case: message not
        found in this conversation, current_status == target_status
        already (idempotent re-ack), or target_status is not in
        ALLOWED_TRANSITIONS[current_status] (a regressive or otherwise
        invalid transition per the forward-only guard). None is a
        signal to receive() to broadcast nothing -- never an error.
        """
        try:
            message = Message.objects.get(
                id=message_id, conversation_id=self.conversation_id
            )
        except Message.DoesNotExist:
            return None

        current_status = message.status
        if current_status == target_status:
            return None

        if target_status not in ALLOWED_TRANSITIONS.get(current_status, set()):
            return None

        message.status = target_status
        message.save(update_fields=["status", "updated_at"])
        return target_status
