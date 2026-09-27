import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch

from chat.models import Conversation, ConversationParticipant, Message

User = get_user_model()

pytestmark = pytest.mark.django_db


def create_user(username, account_type):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        account_type=account_type,
    )


def test_start_conversation_creates_new_conversation():
    user_a = create_user("user_a", "customer")
    user_b = create_user("user_b", "customer")

    client = APIClient()
    client.force_authenticate(user=user_a)

    url = reverse("chat:conversation-start")
    response = client.post(url, {"recipient_id": user_b.id}, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert Conversation.objects.count() == 1
    assert ConversationParticipant.objects.count() == 2


def test_start_conversation_deduplicates_both_directions():
    user_a = create_user("user_a2", "customer")
    user_b = create_user("user_b2", "business")

    client = APIClient()
    url = reverse("chat:conversation-start")

    client.force_authenticate(user=user_a)
    first_response = client.post(url, {"recipient_id": user_b.id}, format="json")
    conversation_id = first_response.data["id"]

    client.force_authenticate(user=user_a)
    second_response = client.post(url, {"recipient_id": user_b.id}, format="json")
    assert second_response.data["id"] == conversation_id

    client.force_authenticate(user=user_b)
    third_response = client.post(url, {"recipient_id": user_a.id}, format="json")
    assert third_response.data["id"] == conversation_id

    assert Conversation.objects.count() == 1


@pytest.mark.parametrize(
    "type_a,type_b",
    [
        ("customer", "customer"),
        ("business", "business"),
        ("customer", "business"),
    ],
)
def test_start_conversation_any_account_type_combination(type_a, type_b):
    user_a = create_user(f"user_{type_a}_a", type_a)
    user_b = create_user(f"user_{type_b}_b", type_b)

    client = APIClient()
    client.force_authenticate(user=user_a)

    url = reverse("chat:conversation-start")
    response = client.post(url, {"recipient_id": user_b.id}, format="json")

    assert response.status_code == status.HTTP_201_CREATED


def test_message_default_status_is_sent():
    user_a = create_user("sender", "customer")
    user_b = create_user("receiver", "customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user_a)
    ConversationParticipant.objects.create(conversation=conversation, user=user_b)

    message = Message.objects.create(
        conversation=conversation, sender=user_a, text="hello"
    )

    assert message.status == Message.Status.SENT


def test_send_message_persists_and_returns_201():
    user_a = create_user("msg_sender", "customer")
    user_b = create_user("msg_receiver", "customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user_a)
    ConversationParticipant.objects.create(conversation=conversation, user=user_b)

    client = APIClient()
    client.force_authenticate(user=user_a)

    url = reverse(
        "chat:conversation-messages", kwargs={"conversation_id": conversation.id}
    )
    response = client.post(url, {"text": "hello there"}, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["text"] == "hello there"
    assert response.data["status"] == Message.Status.SENT
    assert Message.objects.filter(
        conversation=conversation, sender=user_a, text="hello there"
    ).exists()


def test_send_message_rejects_non_participant():
    user_a = create_user("msg_part_a", "customer")
    user_b = create_user("msg_part_b", "customer")
    outsider = create_user("msg_outsider", "customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user_a)
    ConversationParticipant.objects.create(conversation=conversation, user=user_b)

    client = APIClient()
    client.force_authenticate(user=outsider)

    url = reverse(
        "chat:conversation-messages", kwargs={"conversation_id": conversation.id}
    )
    response = client.post(url, {"text": "sneaky"}, format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not Message.objects.filter(text="sneaky").exists()


def test_send_message_returns_404_for_unknown_conversation():
    user_a = create_user("msg_404_a", "customer")

    client = APIClient()
    client.force_authenticate(user=user_a)

    url = reverse("chat:conversation-messages", kwargs={"conversation_id": 999999})
    response = client.post(url, {"text": "hi"}, format="json")

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_send_message_persists_even_when_broadcast_fails():
    """
    THE CRITICAL RELIABILITY TEST for Part P-068.

    Mocks chat.views.async_to_sync itself — the exact call site used
    as async_to_sync(channel_layer.group_send)(...) inside
    MessageSendView.post() — to raise, forcing the broadcast attempt
    to fail regardless of which channel layer backend is configured
    for tests. Proves the persistence-first ordering is genuinely
    independent of broadcast success: the response must still be 201
    and the Message row must still exist.
    """
    user_a = create_user("reliability_sender", "customer")
    user_b = create_user("reliability_receiver", "customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user_a)
    ConversationParticipant.objects.create(conversation=conversation, user=user_b)

    client = APIClient()
    client.force_authenticate(user=user_a)

    url = reverse(
        "chat:conversation-messages", kwargs={"conversation_id": conversation.id}
    )

    with patch(
        "chat.views.async_to_sync",
        side_effect=RuntimeError("simulated channel layer failure"),
    ):
        response = client.post(
            url, {"text": "still here even if broadcast dies"}, format="json"
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["text"] == "still here even if broadcast dies"
    assert Message.objects.filter(
        conversation=conversation,
        sender=user_a,
        text="still here even if broadcast dies",
    ).exists()

def test_send_message_dispatches_notification_when_recipient_offline():
    user_a = create_user("offline_test_sender", "customer")
    user_b = create_user("offline_test_receiver", "customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user_a)
    ConversationParticipant.objects.create(conversation=conversation, user=user_b)

    client = APIClient()
    client.force_authenticate(user=user_a)

    url = reverse(
        "chat:conversation-messages", kwargs={"conversation_id": conversation.id}
    )

    # user_b مفيش presence key ليه في الـ cache خالص = يعتبر offline.
    with patch("chat.views.notify_offline_recipient.delay") as mock_delay:
        response = client.post(url, {"text": "are you there?"}, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    message_id = response.data["id"]
    mock_delay.assert_called_once_with(message_id)


def test_send_message_does_not_dispatch_notification_when_recipient_online():
    from django.core.cache import cache
    from chat.consumers import presence_cache_key

    user_a = create_user("online_test_sender", "customer")
    user_b = create_user("online_test_receiver", "customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user_a)
    ConversationParticipant.objects.create(conversation=conversation, user=user_b)

    cache.set(presence_cache_key(user_b.id), True, 60)

    client = APIClient()
    client.force_authenticate(user=user_a)

    url = reverse(
        "chat:conversation-messages", kwargs={"conversation_id": conversation.id}
    )

    with patch("chat.views.notify_offline_recipient.delay") as mock_delay:
        response = client.post(url, {"text": "no push needed"}, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    mock_delay.assert_not_called()

    cache.delete(presence_cache_key(user_b.id))
    
def test_fetch_since_returns_exactly_messages_after_cursor_ordered():
    user_a = create_user("fetch_since_a", "customer")
    user_b = create_user("fetch_since_b", "customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user_a)
    ConversationParticipant.objects.create(conversation=conversation, user=user_b)

    msg1 = Message.objects.create(conversation=conversation, sender=user_a, text="one")
    msg2 = Message.objects.create(conversation=conversation, sender=user_b, text="two")
    msg3 = Message.objects.create(conversation=conversation, sender=user_a, text="three")

    client = APIClient()
    client.force_authenticate(user=user_b)

    url = reverse(
        "chat:conversation-messages", kwargs={"conversation_id": conversation.id}
    )
    response = client.get(url, {"since": msg1.id})

    assert response.status_code == status.HTTP_200_OK
    returned_ids = [item["id"] for item in response.data]
    assert returned_ids == [msg2.id, msg3.id]  # ordered chronologically, exact set


def test_fetch_since_returns_empty_list_when_nothing_missed():
    user_a = create_user("fetch_since_empty_a", "customer")
    user_b = create_user("fetch_since_empty_b", "customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user_a)
    ConversationParticipant.objects.create(conversation=conversation, user=user_b)

    msg1 = Message.objects.create(conversation=conversation, sender=user_a, text="only")

    client = APIClient()
    client.force_authenticate(user=user_b)

    url = reverse(
        "chat:conversation-messages", kwargs={"conversation_id": conversation.id}
    )
    response = client.get(url, {"since": msg1.id})

    assert response.status_code == status.HTTP_200_OK
    assert response.data == []


def test_fetch_since_rejects_non_participant_with_403():
    user_a = create_user("fetch_since_perm_a", "customer")
    user_b = create_user("fetch_since_perm_b", "customer")
    outsider = create_user("fetch_since_outsider", "customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user_a)
    ConversationParticipant.objects.create(conversation=conversation, user=user_b)
    msg1 = Message.objects.create(conversation=conversation, sender=user_a, text="hi")

    client = APIClient()
    client.force_authenticate(user=outsider)

    url = reverse(
        "chat:conversation-messages", kwargs={"conversation_id": conversation.id}
    )
    response = client.get(url, {"since": msg1.id})

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_fetch_since_returns_404_for_unknown_conversation():
    user_a = create_user("fetch_since_404", "customer")

    client = APIClient()
    client.force_authenticate(user=user_a)

    url = reverse("chat:conversation-messages", kwargs={"conversation_id": 999999})
    response = client.get(url, {"since": 0})

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_fetch_since_requires_since_param():
    user_a = create_user("fetch_since_missing_a", "customer")
    user_b = create_user("fetch_since_missing_b", "customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user_a)
    ConversationParticipant.objects.create(conversation=conversation, user=user_b)

    client = APIClient()
    client.force_authenticate(user=user_a)

    url = reverse(
        "chat:conversation-messages", kwargs={"conversation_id": conversation.id}
    )
    response = client.get(url)

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_fetch_since_rejects_non_integer_since():
    user_a = create_user("fetch_since_bad_a", "customer")
    user_b = create_user("fetch_since_bad_b", "customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user_a)
    ConversationParticipant.objects.create(conversation=conversation, user=user_b)

    client = APIClient()
    client.force_authenticate(user=user_a)

    url = reverse(
        "chat:conversation-messages", kwargs={"conversation_id": conversation.id}
    )
    response = client.get(url, {"since": "not-a-number"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_send_message_still_works_after_dispatcher_change():
    """
    Regression check specific to P-072's dispatcher change: POST على
    نفس الـ /messages/ URL لازم يفضل شغّال زي ما هو بعد ما بقى فيه GET
    handler على نفس المسار.
    """
    user_a = create_user("dispatcher_regression_a", "customer")
    user_b = create_user("dispatcher_regression_b", "customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user_a)
    ConversationParticipant.objects.create(conversation=conversation, user=user_b)

    client = APIClient()
    client.force_authenticate(user=user_a)

    url = reverse(
        "chat:conversation-messages", kwargs={"conversation_id": conversation.id}
    )
    response = client.post(url, {"text": "post still works"}, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["text"] == "post still works"