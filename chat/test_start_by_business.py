"""
Tests for starting a conversation by business_id (Part P-077, backend
half of activating the product screen's "Message Business" button).

Covers: creating a conversation with a business's owner, resuming an
existing one (including one started earlier through the legacy
recipient_id path), the 404 cases (unknown / soft-deleted / inactive
owner), the 400 cases (own business, both identifiers, neither,
non-integer), authentication, and that the recipient_id path still
behaves exactly as before.
"""

from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from businesses.models import BusinessProfile
from chat.models import Conversation

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


def _client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _url():
    return reverse("chat:conversation-start")


def test_business_id_creates_conversation_with_the_business_owner():
    customer = _make_user("customer")
    business = _make_business()

    response = _client_for(customer).post(
        _url(), {"business_id": business.id}, format="json"
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert sorted(response.data["participant_ids"]) == sorted(
        [customer.id, business.user_id]
    )
    assert Conversation.objects.count() == 1


def test_business_id_resumes_the_same_conversation_on_repeat():
    customer = _make_user("customer")
    business = _make_business()
    client = _client_for(customer)

    first = client.post(_url(), {"business_id": business.id}, format="json")
    second = client.post(_url(), {"business_id": business.id}, format="json")

    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_200_OK
    assert second.data["id"] == first.data["id"]
    assert Conversation.objects.count() == 1


def test_business_id_resumes_a_conversation_started_via_recipient_id():
    customer = _make_user("customer")
    business = _make_business()
    client = _client_for(customer)

    legacy = client.post(_url(), {"recipient_id": business.user_id}, format="json")
    by_business = client.post(_url(), {"business_id": business.id}, format="json")

    assert legacy.status_code == status.HTTP_201_CREATED
    assert by_business.status_code == status.HTTP_200_OK
    assert by_business.data["id"] == legacy.data["id"]
    assert Conversation.objects.count() == 1


def test_business_owner_can_be_resumed_from_the_owners_side_too():
    customer = _make_user("customer")
    business = _make_business()

    created = _client_for(customer).post(
        _url(), {"business_id": business.id}, format="json"
    )
    from_owner = _client_for(business.user).post(
        _url(), {"recipient_id": customer.id}, format="json"
    )

    assert from_owner.status_code == status.HTTP_200_OK
    assert from_owner.data["id"] == created.data["id"]


def test_unknown_business_returns_404_and_creates_nothing():
    customer = _make_user("customer")

    response = _client_for(customer).post(
        _url(), {"business_id": 999999}, format="json"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert Conversation.objects.count() == 0


def test_soft_deleted_business_returns_404_and_creates_nothing():
    customer = _make_user("customer")
    business = _make_business()
    business.delete()  # SoftDeleteModel: flags the row, keeps it in the DB

    response = _client_for(customer).post(
        _url(), {"business_id": business.id}, format="json"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert Conversation.objects.count() == 0


def test_business_with_inactive_owner_returns_404_and_creates_nothing():
    customer = _make_user("customer")
    business = _make_business()
    business.user.is_active = False
    business.user.save(update_fields=["is_active"])

    response = _client_for(customer).post(
        _url(), {"business_id": business.id}, format="json"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert Conversation.objects.count() == 0


def test_own_business_returns_400_and_creates_nothing():
    business = _make_business()

    response = _client_for(business.user).post(
        _url(), {"business_id": business.id}, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "yourself" in response.data["detail"]
    assert Conversation.objects.count() == 0


def test_both_identifiers_return_400():
    customer = _make_user("customer")
    business = _make_business()

    response = _client_for(customer).post(
        _url(),
        {"business_id": business.id, "recipient_id": business.user_id},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Conversation.objects.count() == 0


def test_neither_identifier_returns_400():
    customer = _make_user("customer")

    response = _client_for(customer).post(_url(), {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Conversation.objects.count() == 0


def test_non_integer_business_id_returns_400():
    customer = _make_user("customer")

    response = _client_for(customer).post(
        _url(), {"business_id": "not-a-number"}, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Conversation.objects.count() == 0


def test_business_id_requires_authentication():
    business = _make_business()

    response = APIClient().post(_url(), {"business_id": business.id}, format="json")

    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )
    assert Conversation.objects.count() == 0


def test_recipient_id_path_is_unchanged():
    customer = _make_user("customer")
    other = _make_user("other")
    client = _client_for(customer)

    created = client.post(_url(), {"recipient_id": other.id}, format="json")
    again = client.post(_url(), {"recipient_id": other.id}, format="json")
    missing = client.post(_url(), {"recipient_id": 999999}, format="json")
    myself = client.post(_url(), {"recipient_id": customer.id}, format="json")

    assert created.status_code == status.HTTP_201_CREATED
    assert again.status_code == status.HTTP_200_OK
    assert again.data["id"] == created.data["id"]
    assert missing.status_code == status.HTTP_404_NOT_FOUND
    assert myself.status_code == status.HTTP_400_BAD_REQUEST
