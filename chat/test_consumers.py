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
#     Outbound (recipient -> server): {"type": "mark_delivered", "message_id": <id>}
#                                      {"type": "mark_read", "message_id": <id>}
#     Broadcast (server -> both connected clients): {"message_id": <id>, "status": "delivered"|"read"}


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