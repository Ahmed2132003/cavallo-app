"""
Tests for chat media messages (Part P-076, backend half).

Proves media validation goes through the SHARED core.media.validate_upload()
(spied via wraps=, not reimplemented), the size caps (5 MB image /
25 MB video), that a media-only message is valid, and that a message with
neither text nor media is rejected.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from chat.models import Conversation, ConversationParticipant, Message
from chat.tasks import notify_offline_recipient
from core.media import validate_upload
from core.tests.test_media import _DISGUISED_EXE_BYTES, _VALID_PNG_BYTES

User = get_user_model()

pytestmark = pytest.mark.django_db

_VALID_MP4_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
_MB = 1024 * 1024


def _make_user(username):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        account_type="customer",
    )


def _setup(prefix):
    sender = _make_user(f"{prefix}_sender")
    recipient = _make_user(f"{prefix}_recipient")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=sender)
    ConversationParticipant.objects.create(conversation=conversation, user=recipient)
    client = APIClient()
    client.force_authenticate(user=sender)
    url = reverse(
        "chat:conversation-messages", kwargs={"conversation_id": conversation.id}
    )
    return client, url, conversation, sender, recipient


def _png(name="photo.png", extra=0):
    return SimpleUploadedFile(
        name, _VALID_PNG_BYTES + b"\0" * extra, content_type="image/png"
    )


def _mp4(name="clip.mp4", extra=0):
    return SimpleUploadedFile(
        name, _VALID_MP4_BYTES + b"\0" * extra, content_type="video/mp4"
    )


def test_media_only_image_message_succeeds_via_shared_validate_upload():
    client, url, conversation, sender, _ = _setup("img_only")

    with patch("chat.serializers.validate_upload", wraps=validate_upload) as spy:
        response = client.post(url, {"media": _png()}, format="multipart")

    assert response.status_code == status.HTTP_201_CREATED
    assert spy.called  # the shared function ran — not a reimplementation
    assert response.data["media_type"] == "image"
    assert response.data["media"]
    assert response.data["text"] == ""
    message = Message.objects.get(pk=response.data["id"])
    assert message.conversation_id == conversation.id
    assert message.sender_id == sender.id
    assert message.media_type == Message.MediaType.IMAGE
    assert message.media


def test_video_message_with_caption_succeeds():
    client, url, _, _, _ = _setup("vid_caption")

    response = client.post(
        url, {"text": "check this", "media": _mp4()}, format="multipart"
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["media_type"] == "video"
    assert response.data["text"] == "check this"
    assert response.data["media"]


def test_text_only_message_still_works_and_has_no_media():
    client, url, _, _, _ = _setup("text_only")

    response = client.post(url, {"text": "just text"}, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["text"] == "just text"
    assert response.data["media"] is None
    assert response.data["media_type"] == ""


def test_disguised_executable_is_rejected_via_shared_validate_upload():
    client, url, conversation, _, _ = _setup("bad_mime")
    fake = SimpleUploadedFile(
        "photo.png", _DISGUISED_EXE_BYTES, content_type="image/png"
    )

    with patch("chat.serializers.validate_upload", wraps=validate_upload) as spy:
        response = client.post(url, {"media": fake}, format="multipart")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["error"]["code"] == "VALIDATION_ERROR"
    assert spy.called
    assert not Message.objects.filter(conversation=conversation).exists()


def test_image_over_5mb_is_rejected():
    client, url, conversation, _, _ = _setup("big_img")

    response = client.post(url, {"media": _png(extra=5 * _MB)}, format="multipart")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["error"]["code"] == "VALIDATION_ERROR"
    assert not Message.objects.filter(conversation=conversation).exists()


def test_video_between_5mb_and_25mb_is_accepted():
    # Proves video gets the LARGER cap (a 5MB+ file would fail as an image).
    client, url, _, _, _ = _setup("mid_vid")

    response = client.post(url, {"media": _mp4(extra=5 * _MB + 1)}, format="multipart")

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["media_type"] == "video"


def test_video_over_25mb_is_rejected():
    client, url, conversation, _, _ = _setup("big_vid")

    response = client.post(url, {"media": _mp4(extra=25 * _MB)}, format="multipart")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["error"]["code"] == "VALIDATION_ERROR"
    assert not Message.objects.filter(conversation=conversation).exists()


def test_message_with_neither_text_nor_media_is_rejected():
    client, url, conversation, _, _ = _setup("empty_msg")

    response = client.post(url, {"text": "   "}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["error"]["code"] == "VALIDATION_ERROR"
    assert not Message.objects.filter(conversation=conversation).exists()


def test_non_participant_cannot_send_media():
    _, url, conversation, _, _ = _setup("idor")
    outsider = _make_user("idor_outsider")
    outsider_client = APIClient()
    outsider_client.force_authenticate(user=outsider)

    response = outsider_client.post(url, {"media": _png()}, format="multipart")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not Message.objects.filter(conversation=conversation).exists()


def test_conversation_list_last_message_exposes_media_type():
    client, _, conversation, sender, _ = _setup("list_media")
    Message.objects.create(
        conversation=conversation,
        sender=sender,
        media_type=Message.MediaType.IMAGE,
    )

    response = client.get(reverse("chat:conversation-list"))

    assert response.status_code == status.HTTP_200_OK
    last_message = response.data[0]["last_message"]
    assert last_message["media_type"] == "image"
    assert last_message["text"] == ""


def test_offline_push_body_falls_back_to_media_label_when_text_blank(dispatch_delay):
    _, _, conversation, sender, recipient = _setup("push_media")
    message = Message.objects.create(
        conversation=conversation,
        sender=sender,
        media_type=Message.MediaType.VIDEO,
    )

    notify_offline_recipient(message.id)

    dispatch_delay.assert_called_once()
    assert dispatch_delay.call_args.kwargs["body"] == "Sent a video"
    assert dispatch_delay.call_args.kwargs["recipient_id"] == recipient.id
    assert dispatch_delay.call_args.kwargs["notification_type"] == "chat_message"
