"""
Tests for sharing platform content into chat (Part P-077, backend half).

Covers: sending a Post / Reel / Product reference (alone and together with
text), retrieval of the resolved shared content over REST, the visibility
whitelist (unpublished / inactive / unknown types are rejected), the
"type + id together" and "never with media" rules, IDOR protection, the
"taken down after sharing" behaviour, the conversation-list and offline-push
fallbacks, and the database-level both-or-neither constraint.
"""

from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from businesses.models import BusinessProfile
from categories.models import Category
from chat.models import Conversation, ConversationParticipant, Message
from chat.tasks import notify_offline_recipient
from content.models import Post, Reel
from core.tests.test_media import _VALID_PNG_BYTES
from moderation.models import Moderatable
from products.models import Product

User = get_user_model()

pytestmark = pytest.mark.django_db


def _make_user(prefix, account_type="customer"):
    unique = uuid4().hex[:10]
    return User.objects.create_user(
        username=f"{prefix}-{unique}",
        email=f"{prefix}-{unique}@example.com",
        password="testpass123",
        account_type=account_type,
    )


def _make_business(name="Acme Trading"):
    return BusinessProfile.objects.create(
        user=_make_user("owner", "business"),
        business_name=name,
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )


def _publish(obj):
    """Make a Post/Reel visible to published_objects without the moderation
    queue (queryset.update() fires no signals) — same helper idea as
    social/tests/test_api.py."""
    fields = {"status": Moderatable.Status.PUBLISHED}
    if isinstance(obj, Reel):
        fields["processing_status"] = Reel.ProcessingStatus.READY
    type(obj).objects.filter(pk=obj.pk).update(**fields)
    obj.refresh_from_db()
    return obj


def _make_post(business=None, caption="Summer collection", published=True):
    post = Post.objects.create(business=business or _make_business(), caption=caption)
    return _publish(post) if published else post


def _make_reel(business=None, caption="Factory tour", published=True):
    reel = Reel.objects.create(
        business=business or _make_business(),
        caption=caption,
        video=SimpleUploadedFile(
            "raw.mp4",
            b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom",
            content_type="video/mp4",
        ),
    )
    return _publish(reel) if published else reel


def _make_product(business=None, name="Classic Shirt", **overrides):
    defaults = {
        "business": business or _make_business(),
        "category": Category.objects.create(name=f"Fashion-{uuid4().hex[:6]}"),
        "name": name,
        "description": "A shirt.",
        "price": "199.99",
        "currency": Product.CURRENCY_EGP,
    }
    defaults.update(overrides)
    return Product.objects.create(**defaults)


def _setup():
    sender = _make_user("sender")
    recipient = _make_user("recipient")
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=sender)
    ConversationParticipant.objects.create(conversation=conversation, user=recipient)
    client = APIClient()
    client.force_authenticate(user=sender)
    url = reverse(
        "chat:conversation-messages", kwargs={"conversation_id": conversation.id}
    )
    return client, url, conversation, sender, recipient


def test_share_published_post_resolves_shared_content():
    client, url, conversation, sender, _ = _setup()
    business = _make_business("Acme Trading")
    post = _make_post(business, caption="Summer collection")

    response = client.post(
        url,
        {"shared_content_type": "post", "shared_object_id": post.id},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    shared = response.data["shared_content"]
    assert shared["content_type"] == "post"
    assert shared["object_id"] == post.id
    assert shared["available"] is True
    assert shared["business_id"] == business.id
    assert shared["business_name"] == "Acme Trading"
    assert shared["preview"]["preview_text"] == "Summer collection"
    # The two input fields are write-only — never echoed back.
    assert "shared_content_type" not in response.data
    assert "shared_object_id" not in response.data
    message = Message.objects.get(pk=response.data["id"])
    assert message.conversation_id == conversation.id
    assert message.sender_id == sender.id
    assert message.text == ""
    assert message.shared_content == post  # the GenericForeignKey resolves


def test_share_reel_and_product_succeed():
    client, url, _, _, _ = _setup()
    reel = _make_reel(caption="Factory tour")
    product = _make_product(name="Classic Shirt")

    reel_response = client.post(
        url,
        {"shared_content_type": "reel", "shared_object_id": reel.id},
        format="json",
    )
    product_response = client.post(
        url,
        {"shared_content_type": "product", "shared_object_id": product.id},
        format="json",
    )

    assert reel_response.status_code == status.HTTP_201_CREATED
    assert reel_response.data["shared_content"]["content_type"] == "reel"
    assert reel_response.data["shared_content"]["preview"]["preview_text"] == (
        "Factory tour"
    )
    assert product_response.status_code == status.HTTP_201_CREATED
    assert product_response.data["shared_content"]["content_type"] == "product"
    assert product_response.data["shared_content"]["business_id"] == (
        product.business_id
    )
    assert product_response.data["shared_content"]["preview"]["preview_text"] == (
        "Classic Shirt"
    )


def test_text_together_with_shared_content_is_allowed():
    client, url, _, _, _ = _setup()
    product = _make_product()

    response = client.post(
        url,
        {
            "text": "check this out!",
            "shared_content_type": "product",
            "shared_object_id": product.id,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["text"] == "check this out!"
    assert response.data["shared_content"]["object_id"] == product.id


def test_text_only_message_has_null_shared_content():
    client, url, _, _, _ = _setup()

    response = client.post(url, {"text": "hello"}, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["shared_content"] is None


def test_shared_content_is_resolved_on_retrieval():
    client, url, _, _, _ = _setup()
    post = _make_post(caption="Retrievable")
    sent = client.post(
        url,
        {"shared_content_type": "post", "shared_object_id": post.id},
        format="json",
    )
    assert sent.status_code == status.HTTP_201_CREATED

    fetched = client.get(url, {"since": 0})

    assert fetched.status_code == status.HTTP_200_OK
    assert len(fetched.data) == 1
    shared = fetched.data[0]["shared_content"]
    assert shared["content_type"] == "post"
    assert shared["object_id"] == post.id
    assert shared["available"] is True
    assert shared["preview"]["preview_text"] == "Retrievable"


def test_unpublished_post_returns_404_and_creates_nothing():
    client, url, _, _, _ = _setup()
    post = _make_post(published=False)  # pending_review

    response = client.post(
        url,
        {"shared_content_type": "post", "shared_object_id": post.id},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert Message.objects.count() == 0


def test_inactive_product_returns_404_and_creates_nothing():
    client, url, _, _, _ = _setup()
    product = _make_product(is_active=False)

    response = client.post(
        url,
        {"shared_content_type": "product", "shared_object_id": product.id},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert Message.objects.count() == 0


def test_unknown_object_id_returns_404():
    client, url, _, _, _ = _setup()

    response = client.post(
        url,
        {"shared_content_type": "post", "shared_object_id": 999999},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert Message.objects.count() == 0


def test_type_outside_the_whitelist_returns_400():
    client, url, _, sender, _ = _setup()

    response = client.post(
        url,
        {"shared_content_type": "user", "shared_object_id": sender.id},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Message.objects.count() == 0


def test_type_without_id_or_id_without_type_returns_400():
    client, url, _, _, _ = _setup()
    post = _make_post()

    only_type = client.post(url, {"shared_content_type": "post"}, format="json")
    only_id = client.post(url, {"shared_object_id": post.id}, format="json")

    assert only_type.status_code == status.HTTP_400_BAD_REQUEST
    assert only_id.status_code == status.HTTP_400_BAD_REQUEST
    assert Message.objects.count() == 0


def test_shared_content_together_with_media_returns_400():
    client, url, _, _, _ = _setup()
    post = _make_post()
    image = SimpleUploadedFile("photo.png", _VALID_PNG_BYTES, content_type="image/png")

    response = client.post(
        url,
        {
            "media": image,
            "shared_content_type": "post",
            "shared_object_id": post.id,
        },
        format="multipart",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Message.objects.count() == 0


def test_message_with_no_text_media_or_shared_content_returns_400():
    client, url, _, _, _ = _setup()

    response = client.post(url, {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_non_participant_cannot_share_into_conversation():
    _, url, _, _, _ = _setup()
    post = _make_post()
    stranger = APIClient()
    stranger.force_authenticate(user=_make_user("stranger"))

    response = stranger.post(
        url,
        {"shared_content_type": "post", "shared_object_id": post.id},
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert Message.objects.count() == 0


def test_content_taken_down_after_sharing_becomes_unavailable():
    client, url, _, _, _ = _setup()
    post = _make_post(caption="Will be taken down")
    sent = client.post(
        url,
        {"shared_content_type": "post", "shared_object_id": post.id},
        format="json",
    )
    assert sent.data["shared_content"]["available"] is True

    Post.objects.filter(pk=post.pk).update(status=Moderatable.Status.REJECTED)
    fetched = client.get(url, {"since": 0})

    shared = fetched.data[0]["shared_content"]
    assert shared["content_type"] == "post"
    assert shared["object_id"] == post.id
    assert shared["available"] is False
    assert shared["preview"] is None
    assert shared["business_name"] is None


def test_conversation_list_last_message_exposes_shared_content_type():
    client, url, _, _, _ = _setup()
    product = _make_product()
    client.post(
        url,
        {"shared_content_type": "product", "shared_object_id": product.id},
        format="json",
    )

    response = client.get(reverse("chat:conversation-list"))

    assert response.status_code == status.HTTP_200_OK
    last_message = response.data[0]["last_message"]
    assert last_message["shared_content_type"] == "product"
    assert last_message["text"] == ""


def test_offline_push_body_falls_back_to_shared_content_label(dispatch_delay):
    _, _, conversation, sender, recipient = _setup()
    post = _make_post()
    message = Message.objects.create(
        conversation=conversation,
        sender=sender,
        shared_content_type=ContentType.objects.get_for_model(Post),
        shared_object_id=post.id,
    )

    notify_offline_recipient(message.id)

    dispatch_delay.assert_called_once()
    assert dispatch_delay.call_args.kwargs["recipient_id"] == recipient.id
    assert dispatch_delay.call_args.kwargs["body"] == "Shared a post"


def test_database_rejects_a_half_set_shared_reference():
    _, _, conversation, sender, _ = _setup()

    with pytest.raises(IntegrityError), transaction.atomic():
        Message.objects.create(
            conversation=conversation,
            sender=sender,
            text="broken",
            shared_content_type=ContentType.objects.get_for_model(Post),
        )
