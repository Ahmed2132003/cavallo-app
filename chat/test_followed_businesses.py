"""
Tests for the "New chat" picker's backend half:

- GET /api/v1/conversations/following/ lists the businesses the current
  user follows (and only those), excluding soft-deleted businesses,
  inactive owners and the user's own business.
- ConversationListSerializer shows a business / customer name, and never
  the raw e-mail address, as the other participant's display_name.
"""

from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from businesses.models import BusinessProfile, CustomerProfile
from chat.models import Conversation, ConversationParticipant
from social.models import Follow

User = get_user_model()

pytestmark = pytest.mark.django_db


def _make_user(prefix, account_type="customer", **extra):
    unique = uuid4().hex[:10]
    return User.objects.create_user(
        username=f"{prefix}-{unique}",
        email=f"{prefix}-{unique}@example.com",
        password="testpass123",
        account_type=account_type,
        **extra,
    )


def _make_business(name="Acme Trading", **user_extra):
    return BusinessProfile.objects.create(
        user=_make_user("owner", "business", **user_extra),
        business_name=name,
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )


def _client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _following_url():
    return reverse("chat:followed-businesses")


def _results(response):
    data = response.json()
    return data["results"] if isinstance(data, dict) else data


def test_following_requires_authentication():
    response = APIClient().get(_following_url())
    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


def test_following_lists_only_businesses_the_user_follows():
    me = _make_user("me")
    followed = _make_business("Followed Co")
    _make_business("Not Followed Co")
    Follow.objects.create(follower=me, business=followed)

    response = _client_for(me).get(_following_url())

    assert response.status_code == status.HTTP_200_OK
    rows = _results(response)
    assert [row["business_id"] for row in rows] == [followed.id]
    assert rows[0]["business_name"] == "Followed Co"


def test_following_excludes_soft_deleted_inactive_and_own_business():
    me = _make_user("me", "business")
    own = BusinessProfile.objects.create(
        user=me,
        business_name="My Own Shop",
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )
    deleted = _make_business("Deleted Co")
    inactive = _make_business("Inactive Co")
    visible = _make_business("Visible Co")
    for business in (own, deleted, inactive, visible):
        Follow.objects.create(follower=me, business=business)

    deleted.delete()  # soft delete
    inactive.user.is_active = False
    inactive.user.save()

    rows = _results(_client_for(me).get(_following_url()))

    assert [row["business_id"] for row in rows] == [visible.id]


def test_following_does_not_leak_other_users_follows():
    me = _make_user("me")
    other = _make_user("other")
    Follow.objects.create(follower=other, business=_make_business("Theirs"))

    assert _results(_client_for(me).get(_following_url())) == []


def _conversation_between(user, other):
    conversation = Conversation.objects.create()
    ConversationParticipant.objects.create(conversation=conversation, user=user)
    ConversationParticipant.objects.create(conversation=conversation, user=other)
    return conversation


def _other_display_name(me, other):
    _conversation_between(me, other)
    response = _client_for(me).get(reverse("chat:conversation-list"))
    assert response.status_code == status.HTTP_200_OK
    return _results(response)[0]["other_participant"]["display_name"]


def test_display_name_prefers_business_name():
    me = _make_user("me")
    business = _make_business("Nile Textiles")
    assert _other_display_name(me, business.user) == "Nile Textiles"


def test_display_name_uses_customer_display_name():
    me = _make_user("me")
    other = _make_user("cust")
    CustomerProfile.objects.create(user=other, display_name="Mona Ali")
    assert _other_display_name(me, other) == "Mona Ali"


def test_display_name_without_profile_never_shows_the_email():
    me = _make_user("me")
    other = _make_user("noprofile")
    name = _other_display_name(me, other)
    assert "@" not in name
    assert name == other.email.split("@", 1)[0]


def test_display_name_without_profile_prefers_full_name():
    me = _make_user("me")
    other = _make_user("noprofile", first_name="Omar", last_name="Hassan")
    assert _other_display_name(me, other) == "Omar Hassan"
