"""
WebSocket tests for ChatConsumer (Part P-067, STEP 2).

Exercises the real ASGI application (config.asgi.application, i.e.
JWTAuthMiddlewareStack -> URLRouter -> ChatConsumer) via
channels.testing.WebsocketCommunicator, covering exactly the cases
required by Part P-067's Definition of Done:

    1. A real ConversationParticipant with a valid access token
       connects successfully (connected is True).
    2. An authenticated user who is NOT a participant of the target
       conversation is rejected (close code 4003) — the critical IDOR
       test for this part.
    3. An invalid/malformed token is rejected (close code 4001).
    4. A missing token is rejected (close code 4001) — chat/middleware.py's
       own docstring treats "missing" and "invalid" as the same
       AnonymousUser outcome, so this is included for completeness.

IMPORTANT — why every test is `@pytest.mark.django_db(transaction=True)`:
WebsocketCommunicator drives the consumer on its own thread/event
loop. All of the consumer's ORM access goes through
channels.db.database_sync_to_async, which opens its own DB connection
on yet another thread. Django's *default* `django_db` fixture wraps a
test in an outer transaction that is never committed — a separate
connection would never see that transaction's uncommitted rows.
`transaction=True` switches to real commit/flush (TransactionTestCase)
semantics, so the participants created in each test's setup are
actually visible to the consumer's own connection.
"""

import asyncio

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from rest_framework_simplejwt.tokens import AccessToken

from chat.models import Conversation, ConversationParticipant
from chat.tests import create_user
from config.asgi import application

import json

from asgiref.sync import sync_to_async
from django.urls import reverse
from rest_framework.test import APIClient


@database_sync_to_async
def _make_conversation_with_participants(username_a, username_b):
    user_a = create_user(username_a, "customer")
    user_b = create_user(username_b, "customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user_a)
    ConversationParticipant.objects.create(conversation=conversation, user=user_b)
    return user_a, user_b, conversation


def _access_token_for(user):
    """
    AccessToken.for_user(user) is simplejwt's standard way to mint a
    real access token directly (without going through the HTTP
    /auth/login/ endpoint). Reconstructing this string with
    AccessToken(token) is exactly what chat/middleware.py's
    _get_user_from_token() does, so this exercises the real validation
    path end to end.
    """
    return str(AccessToken.for_user(user))


async def _connect(conversation_id, token):
    """
    Open a WebSocketCommunicator against the real config.asgi.application
    for /ws/conversations/<conversation_id>/, appending ?token=... only
    when a token is given (token=None simulates the query param being
    absent entirely, not just empty).

    Returns (communicator, connected, close_code_or_subprotocol) — see
    channels.testing.WebsocketCommunicator.connect()'s own docstring:
    when connected is False, the second value is the close code the
    consumer closed with.
    """
    path = f"/ws/conversations/{conversation_id}/"
    if token is not None:
        path = f"{path}?token={token}"
    communicator = WebsocketCommunicator(application, path)
    connected, close_code_or_subprotocol = await communicator.connect()
    return communicator, connected, close_code_or_subprotocol


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_participant_with_valid_token_connects():
    user_a, _user_b, conversation = await _make_conversation_with_participants(
        "ws_participant_a", "ws_participant_b"
    )
    token = _access_token_for(user_a)

    communicator, connected, _ = await _connect(conversation.id, token)

    assert connected is True

    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_authenticated_non_participant_is_rejected():
    _user_a, _user_b, conversation = await _make_conversation_with_participants(
        "ws_nonpart_a", "ws_nonpart_b"
    )
    outsider = await database_sync_to_async(create_user)("ws_outsider", "customer")
    token = _access_token_for(outsider)

    communicator, connected, close_code = await _connect(conversation.id, token)

    assert connected is False
    assert close_code == 4003


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_invalid_token_is_rejected():
    _user_a, _user_b, conversation = await _make_conversation_with_participants(
        "ws_invalid_a", "ws_invalid_b"
    )

    communicator, connected, close_code = await _connect(
        conversation.id, "this-is-not-a-real-jwt"
    )

    assert connected is False
    assert close_code == 4001


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_missing_token_is_rejected():
    _user_a, _user_b, conversation = await _make_conversation_with_participants(
        "ws_missing_a", "ws_missing_b"
    )

    communicator, connected, close_code = await _connect(conversation.id, token=None)

    assert connected is False
    assert close_code == 4001


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_connected_participant_receives_broadcast_message():
    """
    End-to-end proof of Part P-068's broadcast half: a participant
    connected over WebSocket to /ws/conversations/<id>/ actually
    receives, in real time, the message that MessageSendView persists
    via the REST endpoint — exercising the full
    persist -> group_send -> chat_message -> self.send() path through
    the real ASGI stack (config.asgi.application), not a mock.
    """
    user_a, _user_b, conversation = await _make_conversation_with_participants(
        "broadcast_sender", "broadcast_receiver"
    )
    receiver_token = _access_token_for(_user_b)

    communicator, connected, _ = await _connect(conversation.id, receiver_token)
    assert connected is True

    @sync_to_async
    def _send_message_via_rest():
        client = APIClient()
        client.force_authenticate(user=user_a)
        url = reverse(
            "chat:conversation-messages",
            kwargs={"conversation_id": conversation.id},
        )
        return client.post(url, {"text": "hello over websocket"}, format="json")

    response = await _send_message_via_rest()
    assert response.status_code == 201

    event = await communicator.receive_from()
    payload = json.loads(event)
    assert payload["text"] == "hello over websocket"

    await communicator.disconnect()


# ---------------------------------------------------------------------------
# Part P-069 -- Delivery-State Machine (sent -> delivered -> read)
# ---------------------------------------------------------------------------
#
# Exact WebSocket event shapes exercised below (locked contract for
# P-073/P-074's Flutter client):
#     Outbound (recipient -> server):
#         {"type": "mark_delivered", "message_id": <id>}
#         {"type": "mark_read", "message_id": <id>}
#     Broadcast (server -> both connected clients):
#         {"message_id": <id>, "status": "delivered"|"read"}


@database_sync_to_async
def _create_message(conversation, sender, text="hello", status=None):
    """
    Directly persists a Message via the ORM (bypassing the REST send
    endpoint, which Part P-068 already covers end to end above) so
    these P-069 tests can start from any status they need, including
    'read', without replaying the full sent->delivered->read
    progression first. status=None keeps the Model's own default
    ('sent').
    """
    from chat.models import Message

    kwargs = {"conversation": conversation, "sender": sender, "text": text}
    if status is not None:
        kwargs["status"] = status
    return Message.objects.create(**kwargs)


@database_sync_to_async
def _get_message_status(message_id):
    from chat.models import Message

    return Message.objects.get(id=message_id).status


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_full_status_progression_sent_to_delivered_to_read():
    """
    The recipient's WebsocketCommunicator sends mark_delivered then
    mark_read for the same message; the sender's WebsocketCommunicator
    must receive a status_update broadcast for each step, and the
    Message's status in the database must actually progress
    sent -> delivered -> read.
    """
    user_a, user_b, conversation = await _make_conversation_with_participants(
        "p069_progress_sender", "p069_progress_receiver"
    )
    message = await _create_message(conversation, user_a)

    sender_communicator, sender_connected, _ = await _connect(
        conversation.id, _access_token_for(user_a)
    )
    assert sender_connected is True

    receiver_communicator, receiver_connected, _ = await _connect(
        conversation.id, _access_token_for(user_b)
    )
    assert receiver_connected is True

    # --- delivered ---
    await receiver_communicator.send_to(
        text_data=json.dumps({"type": "mark_delivered", "message_id": message.id})
    )

    raw = await sender_communicator.receive_from()
    payload = json.loads(raw)
    assert payload == {"message_id": message.id, "status": "delivered"}
    assert await _get_message_status(message.id) == "delivered"

    # --- read ---
    await receiver_communicator.send_to(
        text_data=json.dumps({"type": "mark_read", "message_id": message.id})
    )

    raw = await sender_communicator.receive_from()
    payload = json.loads(raw)
    assert payload == {"message_id": message.id, "status": "read"}
    assert await _get_message_status(message.id) == "read"

    await sender_communicator.disconnect()
    await receiver_communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delivered_ack_after_read_is_a_noop_not_a_regression():
    """
    The regression-guard case required by P-069's Definition of Done:
    a message that is already 'read' must not be pushed back to
    'delivered' by a late-arriving mark_delivered ack, and no
    status_update broadcast claiming a change must be sent since no
    change actually happened.
    """
    user_a, user_b, conversation = await _make_conversation_with_participants(
        "p069_regress_sender", "p069_regress_receiver"
    )
    message = await _create_message(conversation, user_a, status="read")

    sender_communicator, sender_connected, _ = await _connect(
        conversation.id, _access_token_for(user_a)
    )
    assert sender_connected is True

    receiver_communicator, receiver_connected, _ = await _connect(
        conversation.id, _access_token_for(user_b)
    )
    assert receiver_connected is True

    await receiver_communicator.send_to(
        text_data=json.dumps({"type": "mark_delivered", "message_id": message.id})
    )

    # No broadcast at all -- receive_nothing() waits its default
    # timeout and asserts nothing arrived, which is exactly the
    # "silent no-op, not a regression" contract this part requires.
    assert await sender_communicator.receive_nothing() is True
    assert await _get_message_status(message.id) == "read"

    await sender_communicator.disconnect()
    await receiver_communicator.disconnect()


# ---------------------------------------------------------------------------
# Part P-070 -- Presence via Redis (connect/disconnect + heartbeat)
# ---------------------------------------------------------------------------


@database_sync_to_async
def _get_presence(user_id):
    from django.core.cache import cache

    from chat.consumers import presence_cache_key

    return cache.get(presence_cache_key(user_id))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_connect_sets_online_and_disconnect_clears_it_immediately():
    """
    Core P-070 acceptance criteria: a connected participant shows as
    online, and disconnecting clears that immediately -- not waiting
    for the TTL to lapse.
    """
    user_a, _user_b, conversation = await _make_conversation_with_participants(
        "presence_connect_a", "presence_connect_b"
    )

    assert await _get_presence(user_a.id) is None

    communicator, connected, _ = await _connect(
        conversation.id, _access_token_for(user_a)
    )
    assert connected is True
    assert await _get_presence(user_a.id) is True

    await communicator.disconnect()

    assert await _get_presence(user_a.id) is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_rejected_connection_never_sets_presence():
    """
    An authenticated non-participant is rejected (close code 4003, per
    P-067) before ever being accepted -- this must never mark them
    online.
    """
    _user_a, _user_b, conversation = await _make_conversation_with_participants(
        "presence_reject_a", "presence_reject_b"
    )
    outsider = await database_sync_to_async(create_user)(
        "presence_outsider", "customer"
    )

    communicator, connected, close_code = await _connect(
        conversation.id, _access_token_for(outsider)
    )

    assert connected is False
    assert close_code == 4003
    assert await _get_presence(outsider.id) is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_heartbeat_extends_presence_past_original_ttl(monkeypatch):
    """
    Proves the heartbeat genuinely extends the presence window, not
    just that the initial TTL happens to cover the test's runtime.

    PRESENCE_TTL_SECONDS is patched down to 2 seconds so this is fast
    and doesn't actually wait a real 60+ seconds. Timeline:
        t=0.0  connect()          -> online, TTL=2s (expires ~t=2.0)
        t=1.2  send heartbeat     -> TTL refreshed (expires ~t=3.2)
        t=2.4  (past the ORIGINAL 2s window) still online -> heartbeat
               genuinely extended it, proving this isn't just the
               original TTL by coincidence.
    """
    import chat.consumers as consumers_module

    monkeypatch.setattr(consumers_module, "PRESENCE_TTL_SECONDS", 2)

    user_a, _user_b, conversation = await _make_conversation_with_participants(
        "presence_heartbeat_a", "presence_heartbeat_b"
    )

    communicator, connected, _ = await _connect(
        conversation.id, _access_token_for(user_a)
    )
    assert connected is True
    assert await _get_presence(user_a.id) is True

    await asyncio.sleep(1.2)
    await communicator.send_to(text_data=json.dumps({"type": "heartbeat"}))
    # Give receive() a beat to actually process the frame before the
    # next assertion races it.
    await asyncio.sleep(0.1)

    await asyncio.sleep(1.3)  # total elapsed ~2.6s > the original 2s TTL
    assert await _get_presence(user_a.id) is True

    await communicator.disconnect()
    assert await _get_presence(user_a.id) is None


# ---------------------------------------------------------------------------
# Part P-071 -- Typing Indicator (WS-Only, Never Persisted)
# ---------------------------------------------------------------------------
#
# Exact WebSocket event shape exercised below (locked contract for
# P-074's Flutter client):
#     Outbound (typer -> server):
#         {"type": "typing", "is_typing": true|false}
#     Broadcast (server -> every OTHER connected participant):
#         {"is_typing": true|false}
# The sender's own connection must never receive its own typing
# broadcast back.


@database_sync_to_async
def _chat_row_counts():
    """
    Snapshot of every row count across all three chat models
    (Conversation, ConversationParticipant, Message). Used as a
    before/after fingerprint to prove typing events create zero new
    rows anywhere in this app -- not just "no new Message", which
    alone wouldn't catch an accidental write to a different table.
    """
    from chat.models import Conversation, ConversationParticipant, Message

    return (
        Conversation.objects.count(),
        ConversationParticipant.objects.count(),
        Message.objects.count(),
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_typing_event_reaches_other_participant_not_sender():
    """
    Part P-071's core real-time contract: when user_a sends a "typing"
    event, user_b's connected communicator must receive a
    typing_indicator broadcast, and user_a's OWN communicator must
    receive nothing back for its own typing event.
    """
    user_a, user_b, conversation = await _make_conversation_with_participants(
        "p071_typer", "p071_other_participant"
    )

    sender_communicator, sender_connected, _ = await _connect(
        conversation.id, _access_token_for(user_a)
    )
    assert sender_connected is True

    other_communicator, other_connected, _ = await _connect(
        conversation.id, _access_token_for(user_b)
    )
    assert other_connected is True

    await sender_communicator.send_to(
        text_data=json.dumps({"type": "typing", "is_typing": True})
    )

    raw = await other_communicator.receive_from()
    payload = json.loads(raw)
    assert payload == {"is_typing": True}

    # The sender must not receive its own typing broadcast back.
    assert await sender_communicator.receive_nothing() is True

    await sender_communicator.disconnect()
    await other_communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_typing_stopped_event_also_reaches_other_participant():
    """
    The is_typing: false ("stopped typing") case must broadcast just
    as faithfully as is_typing: true -- this consumer must not treat
    False as "nothing to send" (a falsy-value bug that would silently
    break the "X stopped typing..." UI state on the receiving client).
    """
    user_a, user_b, conversation = await _make_conversation_with_participants(
        "p071_typer_stop", "p071_other_stop"
    )

    sender_communicator, sender_connected, _ = await _connect(
        conversation.id, _access_token_for(user_a)
    )
    assert sender_connected is True

    other_communicator, other_connected, _ = await _connect(
        conversation.id, _access_token_for(user_b)
    )
    assert other_connected is True

    await sender_communicator.send_to(
        text_data=json.dumps({"type": "typing", "is_typing": False})
    )

    raw = await other_communicator.receive_from()
    payload = json.loads(raw)
    assert payload == {"is_typing": False}

    await sender_communicator.disconnect()
    await other_communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_malformed_typing_event_is_silently_ignored():
    """
    A "typing" frame whose is_typing is missing or not a real bool
    (e.g. a string) must be a silent no-op, matching the same
    "malformed/unrecognized frame -> ignore" contract receive()
    already applies to every other event type in this consumer --
    never a crash, never a broadcast of garbage data.
    """
    user_a, user_b, conversation = await _make_conversation_with_participants(
        "p071_malformed_a", "p071_malformed_b"
    )

    sender_communicator, sender_connected, _ = await _connect(
        conversation.id, _access_token_for(user_a)
    )
    assert sender_connected is True

    other_communicator, other_connected, _ = await _connect(
        conversation.id, _access_token_for(user_b)
    )
    assert other_connected is True

    await sender_communicator.send_to(
        text_data=json.dumps({"type": "typing", "is_typing": "yes"})
    )
    await sender_communicator.send_to(text_data=json.dumps({"type": "typing"}))

    assert await other_communicator.receive_nothing() is True
    assert await sender_communicator.receive_nothing() is True

    await sender_communicator.disconnect()
    await other_communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_typing_events_never_persist_anything():
    """
    Direct database-state check required by P-071's Definition of
    Done: several typing events (both is_typing: true and false, from
    both participants) must leave every chat-related table's row count
    completely unchanged -- proving zero persistence was actually
    verified, not just assumed from the code not obviously writing
    anything.
    """
    user_a, user_b, conversation = await _make_conversation_with_participants(
        "p071_persist_a", "p071_persist_b"
    )

    communicator_a, connected_a, _ = await _connect(
        conversation.id, _access_token_for(user_a)
    )
    assert connected_a is True

    communicator_b, connected_b, _ = await _connect(
        conversation.id, _access_token_for(user_b)
    )
    assert connected_b is True

    before = await _chat_row_counts()

    await communicator_a.send_to(
        text_data=json.dumps({"type": "typing", "is_typing": True})
    )
    await communicator_b.receive_from()  # drain b's broadcast from a

    await communicator_b.send_to(
        text_data=json.dumps({"type": "typing", "is_typing": True})
    )
    await communicator_a.receive_from()  # drain a's broadcast from b

    await communicator_a.send_to(
        text_data=json.dumps({"type": "typing", "is_typing": False})
    )
    await communicator_b.receive_from()  # drain b's broadcast from a

    after = await _chat_row_counts()
    assert after == before

    await communicator_a.disconnect()
    await communicator_b.disconnect()
