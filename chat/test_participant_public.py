"""
Tests for the shared public user serializer inside the conversation list
(Part P-116A-2): ``other_participant`` carries the other user's
User.display_name / User.avatar, never e-mail or username, and the list
does not add queries per conversation for the participant data.

Storage: the default storage is swapped for Django's InMemoryStorage for
each test (same approach as accounts/tests/test_profile_avatar.py).
"""

import json
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from businesses.models import BusinessProfile, CustomerProfile
from chat.models import Conversation, ConversationParticipant, Message

User = get_user_model()

pytestmark = pytest.mark.django_db

LIST_KEYS = {"id", "other_participant", "last_message", "unread_count", "created_at"}
OTHER_KEYS = {"id", "account_type", "display_name", "avatar"}


@pytest.fixture(autouse=True)
def _in_memory_storage(settings):
    storages = dict(settings.STORAGES)
    storages["default"] = {
        "BACKEND": "django.core.files.storage.InMemoryStorage",
        "OPTIONS": {"base_url": "/media/"},
    }
    settings.STORAGES = storages
    yield


def _make_user(prefix, account_type="customer", **extra):
    unique = uuid4().hex[:10]
    return User.objects.create_user(
        username=f"{prefix}-{unique}",
        email=f"{prefix}-{unique}@example.com",
        password="testpass123",
        account_type=account_type,
        **extra,
    )


def _with_avatar(user):
    user.avatar.save("face.png", ContentFile(b"not-really-a-png"), save=True)
    return user


def _talk(me, other):
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=me)
    ConversationParticipant.objects.create(conversation=conversation, user=other)
    return conversation


def _list(me):
    client = APIClient()
    client.force_authenticate(user=me)
    response = client.get(reverse("chat:conversation-list"))
    assert response.status_code == status.HTTP_200_OK
    return response


def test_other_participant_has_display_name_and_absolute_avatar_url():
    me = _make_user("me")
    other = _with_avatar(_make_user("other", display_name="Mona Ali"))
    _talk(me, other)

    item = _list(me).data[0]["other_participant"]

    assert item["display_name"] == "Mona Ali"
    assert item["avatar"].startswith("http://testserver/media/avatars/")


def test_other_participant_avatar_is_null_when_not_set():
    me = _make_user("me")
    other = _make_user("other", display_name="Mona Ali")
    _talk(me, other)

    item = _list(me).data[0]["other_participant"]

    assert item["display_name"] == "Mona Ali"
    assert item["avatar"] is None


def test_other_participant_exposes_no_email_or_username():
    me = _make_user("me")
    other = _with_avatar(_make_user("other", display_name="Mona Ali"))
    _talk(me, other)

    response = _list(me)

    assert set(response.data[0]["other_participant"].keys()) == OTHER_KEYS
    body = json.dumps(response.json())
    assert other.email not in body
    assert other.username not in body
    for forbidden in ("email", "username", "phone", "is_staff", "is_moderator"):
        assert forbidden not in response.data[0]["other_participant"]


def test_user_without_customer_profile_works():
    me = _make_user("me")
    other = _make_user("noprofile", display_name="Just A Name")
    _talk(me, other)

    item = _list(me).data[0]["other_participant"]

    assert item["display_name"] == "Just A Name"


def test_user_display_name_wins_over_business_name():
    me = _make_user("me")
    owner = _make_user("owner", "business", display_name="Hassan")
    BusinessProfile.objects.create(
        user=owner,
        business_name="Nile Textiles",
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )
    _talk(me, owner)

    assert _list(me).data[0]["other_participant"]["display_name"] == "Hassan"


def test_existing_list_and_participant_keys_are_unchanged():
    me = _make_user("me")
    other = _make_user("other")
    CustomerProfile.objects.create(user=other, display_name="Mona Ali")
    conversation = _talk(me, other)
    Message.objects.create(conversation=conversation, sender=other, text="hi")

    row = _list(me).data[0]

    assert set(row.keys()) == LIST_KEYS
    assert set(row["other_participant"].keys()) == OTHER_KEYS
    assert row["other_participant"]["id"] == other.id
    assert row["other_participant"]["account_type"] == "customer"
    # Old behaviour preserved when the user set no display_name of their own.
    assert row["other_participant"]["display_name"] == "Mona Ali"
    assert row["unread_count"] == 1
    assert row["last_message"]["text"] == "hi"


def _query_count(me):
    with CaptureQueriesContext(connection) as ctx:
        _list(me)
    return ctx.captured_queries


def _make_mixed_conversations(me, count):
    for index in range(count):
        if index % 3 == 0:
            other = _with_avatar(_make_user("c", display_name=f"Own {index}"))
        elif index % 3 == 1:
            other = _make_user("c")
            CustomerProfile.objects.create(user=other, display_name=f"Cust {index}")
        else:
            other = _make_user("b", "business")
            BusinessProfile.objects.create(
                user=other,
                business_name=f"Biz {index}",
                business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
                country="Egypt",
                city="Cairo",
            )
        conversation = _talk(me, other)
        Message.objects.create(conversation=conversation, sender=other, text="hi")


def test_participant_data_adds_no_queries_per_conversation():
    me = _make_user("me")
    _make_mixed_conversations(me, 1)
    small = _query_count(me)

    _make_mixed_conversations(me, 5)  # 6 conversations in total now
    large = _query_count(me)

    def participant_queries(queries):
        return [q for q in queries if "chat_conversationparticipant" in q["sql"]]

    # Participant / user / profile loading is constant, not per conversation.
    assert len(participant_queries(large)) == len(participant_queries(small))
    # The only per-conversation queries left are the two documented, older
    # ones (last_message and unread_count): at most 2 per extra conversation.
    assert len(large) - len(small) <= 2 * 5
