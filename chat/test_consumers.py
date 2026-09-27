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
