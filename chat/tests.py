import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

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
