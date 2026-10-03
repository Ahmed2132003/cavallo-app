"""
P-096 (step 3) permission / IDOR sweep for the chat app's HTTP routes.

Routes (all under /api/v1/conversations/): start (POST), list (GET),
{id}/messages/ (POST send; GET history; GET ?since=), and
users/{id}/presence/ (GET).

The WebSocket side is NOT repeated here: chat/test_consumers.py already
proves the equivalent object-level rules (close code 4001 for a missing /
invalid token, 4003 for an authenticated non-participant) against the
real ASGI application.

Category 1: every route -> 401 envelope (no token and garbage token);
            no Conversation or Message row is created.
Category 2: an authenticated NON-participant gets 403 (envelope) on send,
            history and fetch-since, makes no change, and never sees
            message content; unknown conversation -> 404 envelope; the
            conversation list only ever shows the caller's own
            conversations; a spoofed `sender` / `conversation` / `status`
            in the body is ignored; a third user starting a conversation
            with A can never become a participant of A's existing
            conversation with B; there is no edit/delete route.
Category 3: no capability-gated route exists in this app (chat is
            any-to-any by confirmed decision).

OPEN FINDING S-3 (characterised, NOT changed here): ConversationStartView
builds its 400/404 bodies by hand as {"detail": "..."} instead of raising
DRF exceptions, so those responses do NOT carry the P-012 error envelope
({"error": {"code", "message", "fields"}}). Statuses are right and no
data leaks; only the body shape differs. Fixing it would change what the
Flutter client already parses, so it needs a deliberate decision. The
last tests pin today's shape.

ACCEPTED DESIGN D-2 (characterised): presence is readable by ANY
authenticated user for any existing user id (chat/views.py documents the
decision and says to revisit it). The response is only user_id +
is_online.
"""

from types import SimpleNamespace

import pytest

from chat.models import Conversation, ConversationParticipant, Message
from core.tests.sweep_factories import client_for, garbage_token_client, make_user
from core.tests.sweep_helpers import (
    assert_error_envelope,
    assert_forbidden,
    assert_not_found,
    assert_unauthenticated,
)

pytestmark = pytest.mark.django_db

BASE = "/api/v1/conversations/"
SECRET_TEXT = "private words nobody else may read"


def _conversation(*users):
    conversation = Conversation.objects.create()
    for user in users:
        ConversationParticipant.objects.create(conversation=conversation, user=user)
    return conversation


@pytest.fixture
def world():
    alice, bob, outsider = make_user(), make_user(), make_user()
    conversation = _conversation(alice, bob)
    message = Message.objects.create(
        conversation=conversation, sender=alice, text=SECRET_TEXT
    )
    return SimpleNamespace(
        alice=alice,
        bob=bob,
        outsider=outsider,
        conversation=conversation,
        message=message,
    )


def _messages_url(conversation_id):
    return f"{BASE}{conversation_id}/messages/"


def _requests(w):
    messages = _messages_url(w.conversation.pk)
    return {
        "start": ("post", f"{BASE}start/", {"recipient_id": w.bob.pk}),
        "list": ("get", BASE, None),
        "history": ("get", messages, None),
        "since": ("get", f"{messages}?since=0", None),
        "send": ("post", messages, {"text": "unauthenticated attempt"}),
        "presence": ("get", f"{BASE}users/{w.bob.pk}/presence/", None),
    }


LABELS = ["start", "list", "history", "since", "send", "presence"]


def _send(client, method, url, payload):
    if method == "get":
        return client.get(url)
    return client.post(url, payload or {}, format="json")


def _rows():
    return Conversation.objects.count(), Message.objects.count()


@pytest.mark.parametrize("client_factory", [client_for, garbage_token_client])
@pytest.mark.parametrize("label", LABELS)
def test_unauthenticated_gets_401_and_changes_nothing(world, label, client_factory):
    before = _rows()
    method, url, payload = _requests(world)[label]

    response = _send(client_factory(), method, url, payload)

    assert_unauthenticated(response)
    assert _rows() == before


def test_participant_positive_control_can_read_and_send(world):
    client = client_for(world.bob)

    history = client.get(_messages_url(world.conversation.pk))
    sent = client.post(
        _messages_url(world.conversation.pk), {"text": "hi alice"}, format="json"
    )

    assert history.status_code == 200
    assert SECRET_TEXT in history.content.decode()
    assert sent.status_code == 201


@pytest.mark.parametrize("label", ["history", "since", "send"])
def test_non_participant_gets_403_envelope_sees_nothing_and_changes_nothing(
    world, label
):
    before = _rows()
    method, url, payload = _requests(world)[label]

    response = _send(client_for(world.outsider), method, url, payload)

    assert_forbidden(response)
    assert SECRET_TEXT not in response.content.decode()
    assert _rows() == before
    assert not Message.objects.filter(sender=world.outsider).exists()
    assert not ConversationParticipant.objects.filter(
        conversation=world.conversation, user=world.outsider
    ).exists()


@pytest.mark.parametrize("label", ["history", "since", "send"])
def test_unknown_conversation_is_404_with_envelope_and_changes_nothing(world, label):
    before = _rows()
    method, url, payload = _requests(world)[label]
    url = url.replace(f"/{world.conversation.pk}/", "/999999/")

    response = _send(client_for(world.alice), method, url, payload)

    assert_not_found(response)
    assert _rows() == before


def test_conversation_list_only_contains_the_callers_own_conversations(world):
    other_a, other_b = make_user(), make_user()
    foreign = _conversation(other_a, other_b)

    def listed_ids(user):
        # ConversationListView is not paginated: the body is a plain list.
        response = client_for(user).get(BASE)
        assert response.status_code == 200
        return [item["id"] for item in response.json()]

    assert listed_ids(world.alice) == [world.conversation.pk]
    assert listed_ids(world.outsider) == []
    assert listed_ids(other_a) == [foreign.pk]


def test_spoofed_sender_conversation_and_status_in_the_body_are_ignored(world):
    other = _conversation(make_user(), make_user())

    response = client_for(world.alice).post(
        _messages_url(world.conversation.pk),
        {
            "text": "spoof attempt",
            "sender": world.bob.pk,
            "conversation": other.pk,
            "status": "read",
        },
        format="json",
    )

    assert response.status_code == 201
    message = Message.objects.get(text="spoof attempt")
    assert message.sender_id == world.alice.pk
    assert message.conversation_id == world.conversation.pk
    assert message.status == Message.Status.SENT
    assert Message.objects.filter(conversation=other).count() == 0


def test_third_user_starting_a_chat_never_joins_an_existing_conversation(world):
    response = client_for(world.outsider).post(
        f"{BASE}start/", {"recipient_id": world.alice.pk}, format="json"
    )

    assert response.status_code == 201
    new_id = response.json()["id"]
    assert new_id != world.conversation.pk
    participants = set(
        ConversationParticipant.objects.filter(
            conversation=world.conversation
        ).values_list("user_id", flat=True)
    )
    assert participants == {world.alice.pk, world.bob.pk}
    assert not Message.objects.filter(conversation_id=new_id, text=SECRET_TEXT).exists()


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_messages_collection_has_no_edit_or_delete_handler(world, method):
    response = getattr(client_for(world.alice), method)(
        _messages_url(world.conversation.pk), {"text": "edited"}, format="json"
    )

    assert_error_envelope(response, 405, "METHOD_NOT_ALLOWED")
    world.message.refresh_from_db()
    assert world.message.text == SECRET_TEXT
    assert Message.objects.count() == 1


@pytest.mark.parametrize("actor", ["participant", "outsider"])
@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_no_message_or_conversation_detail_route_exists(world, actor, method):
    user = world.alice if actor == "participant" else world.outsider
    client = client_for(user)

    message_response = getattr(client, method)(
        f"{_messages_url(world.conversation.pk)}{world.message.pk}/"
    )
    conversation_response = getattr(client, method)(f"{BASE}{world.conversation.pk}/")

    assert message_response.status_code == 404
    assert conversation_response.status_code == 404
    assert Message.objects.filter(pk=world.message.pk, text=SECRET_TEXT).exists()
    assert Conversation.objects.filter(pk=world.conversation.pk).exists()


def test_conversation_list_route_has_no_write_handler(world):
    before = _rows()

    response = client_for(world.alice).post(BASE, {}, format="json")

    assert_error_envelope(response, 405, "METHOD_NOT_ALLOWED")
    assert _rows() == before


def test_presence_of_an_unknown_user_is_404_with_envelope(world):
    response = client_for(world.alice).get(f"{BASE}users/999999/presence/")

    assert_not_found(response)


def test_design_d2_presence_is_readable_by_any_authenticated_user(world):
    """Characterisation of accepted design D-2 - see module docstring."""
    response = client_for(world.outsider).get(f"{BASE}users/{world.alice.pk}/presence/")

    assert response.status_code == 200
    assert response.json() == {"user_id": world.alice.pk, "is_online": False}


def test_finding_s3_start_errors_use_detail_not_the_p012_envelope(world):
    client = client_for(world.alice)
    start = f"{BASE}start/"
    before = _rows()

    unknown = client.post(start, {"recipient_id": 999999}, format="json")
    yourself = client.post(start, {"recipient_id": world.alice.pk}, format="json")
    neither = client.post(start, {}, format="json")
    both = client.post(
        start, {"recipient_id": world.bob.pk, "business_id": 1}, format="json"
    )

    assert unknown.status_code == 404
    assert yourself.status_code == 400
    assert neither.status_code == 400
    assert both.status_code == 400
    for response in (unknown, yourself, neither, both):
        assert list(response.json().keys()) == ["detail"]
    assert _rows() == before
