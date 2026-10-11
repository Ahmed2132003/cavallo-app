"""
Business profile photo (avatar):
POST/DELETE /api/v1/businesses/me/avatar/, the read-only `avatar` field on the
owner /me/ and public profile responses, and the chat list's
other_participant.avatar.
"""

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from businesses.models import BusinessProfile
from businesses.services import create_business_profile
from core.tests.test_media import _DISGUISED_EXE_BYTES, _VALID_PNG_BYTES

pytestmark = pytest.mark.django_db

ME_URL = reverse("businesses:business-me")
AVATAR_URL = reverse("businesses:business-me-avatar")


def _make_user(email, account_type="business"):
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type=account_type,
    )


def _make_profile(email="avatar-owner@example.com", name="Avatar Biz"):
    user = _make_user(email)
    profile = create_business_profile(
        user=user,
        business_name=name,
        business_type="trader",
        country="EG",
        city="Cairo",
    )
    return user, profile


def _client_for(user):
    client = APIClient()
    client.force_authenticate(user=User.objects.get(pk=user.pk))
    return client


def _png(name="photo.png"):
    return SimpleUploadedFile(name, _VALID_PNG_BYTES, content_type="image/png")


def test_profile_without_avatar_reads_null_everywhere():
    user, profile = _make_profile()
    client = _client_for(user)

    assert client.get(ME_URL).json()["avatar"] is None
    public = APIClient().get(reverse("businesses:business-public", args=[profile.id]))
    assert public.status_code == 200
    assert public.json()["avatar"] is None


def test_upload_sets_avatar_and_returns_it_on_me_and_public_reads():
    user, profile = _make_profile()
    client = _client_for(user)

    response = client.post(AVATAR_URL, {"avatar": _png()}, format="multipart")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["id"] == profile.id
    assert body["avatar"]
    profile.refresh_from_db()
    assert profile.avatar

    assert client.get(ME_URL).json()["avatar"]
    public = APIClient().get(reverse("businesses:business-public", args=[profile.id]))
    assert public.json()["avatar"]


def test_upload_invalidates_the_public_profile_cache():
    user, profile = _make_profile()
    public_url = reverse("businesses:business-public", args=[profile.id])
    assert APIClient().get(public_url).json()["avatar"] is None  # primes the cache

    _client_for(user).post(AVATAR_URL, {"avatar": _png()}, format="multipart")

    assert APIClient().get(public_url).json()["avatar"]
    cache.clear()


def test_replacing_the_avatar_keeps_a_single_current_photo():
    user, profile = _make_profile()
    client = _client_for(user)
    client.post(AVATAR_URL, {"avatar": _png("one.png")}, format="multipart")
    profile.refresh_from_db()
    first_name = profile.avatar.name

    response = client.post(AVATAR_URL, {"avatar": _png("two.png")}, format="multipart")

    assert response.status_code == 200
    profile.refresh_from_db()
    assert profile.avatar
    assert profile.avatar.name != first_name


def test_delete_removes_the_avatar_and_is_idempotent():
    user, profile = _make_profile()
    client = _client_for(user)
    client.post(AVATAR_URL, {"avatar": _png()}, format="multipart")

    first = client.delete(AVATAR_URL)
    second = client.delete(AVATAR_URL)

    assert first.status_code == 200
    assert first.json()["avatar"] is None
    assert second.status_code == 200
    profile.refresh_from_db()
    assert not profile.avatar


def test_rejects_a_disguised_non_image_file():
    user, profile = _make_profile()
    fake = SimpleUploadedFile(
        "photo.png", _DISGUISED_EXE_BYTES, content_type="image/png"
    )

    response = _client_for(user).post(AVATAR_URL, {"avatar": fake}, format="multipart")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    profile.refresh_from_db()
    assert not profile.avatar


def test_missing_file_is_a_validation_error():
    user, _ = _make_profile()

    response = _client_for(user).post(AVATAR_URL, {}, format="multipart")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unauthenticated_is_401():
    assert APIClient().post(AVATAR_URL, {"avatar": _png()}, format="multipart").status_code == 401
    assert APIClient().delete(AVATAR_URL).status_code == 401


def test_customer_account_cannot_set_a_business_avatar():
    customer = _make_user("avatar-customer@example.com", account_type="customer")

    response = _client_for(customer).post(
        AVATAR_URL, {"avatar": _png()}, format="multipart"
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_business_without_a_profile_yet_gets_a_validation_error():
    user = _make_user("avatar-noprofile@example.com")

    response = _client_for(user).post(AVATAR_URL, {"avatar": _png()}, format="multipart")

    assert response.status_code == 400


def test_avatar_is_not_writable_through_the_json_me_endpoint():
    user, profile = _make_profile()

    response = _client_for(user).patch(
        ME_URL, {"avatar": "https://evil.example/x.png"}, format="json"
    )

    assert response.status_code == 200
    profile.refresh_from_db()
    assert not profile.avatar


def test_a_user_can_only_change_their_own_avatar():
    owner, owner_profile = _make_profile("avatar-a@example.com", "Biz A")
    other, other_profile = _make_profile("avatar-b@example.com", "Biz B")

    _client_for(owner).post(AVATAR_URL, {"avatar": _png()}, format="multipart")

    owner_profile.refresh_from_db()
    other_profile.refresh_from_db()
    assert owner_profile.avatar
    assert not other_profile.avatar


def test_search_style_serializer_exposes_avatar_read_only():
    from businesses.serializers import BusinessProfileSerializer

    assert BusinessProfileSerializer().fields["avatar"].read_only is True
    assert BusinessProfile._meta.get_field("avatar").null is True


def test_chat_list_other_participant_carries_the_business_avatar():
    from chat.models import Conversation, ConversationParticipant

    biz_user, biz_profile = _make_profile("avatar-chat-biz@example.com", "Chat Biz")
    _client_for(biz_user).post(AVATAR_URL, {"avatar": _png()}, format="multipart")
    customer = _make_user("avatar-chat-cust@example.com", account_type="customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=biz_user)
    ConversationParticipant.objects.create(conversation=conversation, user=customer)

    response = _client_for(customer).get(reverse("chat:conversation-list"))

    assert response.status_code == 200
    other = response.data[0]["other_participant"]
    assert other["display_name"] == "Chat Biz"
    assert other["avatar"]


def test_chat_list_customer_participant_has_no_avatar():
    from chat.models import Conversation, ConversationParticipant

    biz_user, _ = _make_profile("avatar-chat-biz2@example.com", "Chat Biz 2")
    customer = _make_user("avatar-chat-cust2@example.com", account_type="customer")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=biz_user)
    ConversationParticipant.objects.create(conversation=conversation, user=customer)

    response = _client_for(biz_user).get(reverse("chat:conversation-list"))

    assert response.status_code == 200
    assert response.data[0]["other_participant"]["avatar"] is None
