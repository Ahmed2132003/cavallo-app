"""
P-096 (step 1) permission / IDOR sweep for the businesses app.

Endpoints: /businesses/me/ (GET, POST, PATCH), /customers/me/ (GET,
POST, PATCH), /businesses/{id}/ (public GET).

Category 1 (401 + envelope), category 2 (no cross-user change; every
profile endpoint resolves from request.user, never from a body id) and
the owner-can't-self-grant-privilege case (the businesses app's only
"capability-gated" surface: verified / featured / follower_count are
Admin-controlled and must be read-only here).

Observation (not changed here): the account-type guard on POST
/businesses/me/ and /customers/me/ answers 400 VALIDATION_ERROR, not
403. That is the contract pinned by P-026's tests; this sweep pins it
too, with the envelope.
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from businesses.models import BusinessProfile, CustomerProfile
from core.tests.sweep_helpers import assert_unauthenticated

pytestmark = pytest.mark.django_db

_BUSINESS_PAYLOAD = {
    "business_name": "Sweep Biz",
    "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
    "country": "Egypt",
    "city": "Cairo",
}
_CUSTOMER_PAYLOAD = {
    "display_name": "Sweep Customer",
    "country": "Egypt",
    "city": "Cairo",
}


@pytest.fixture
def api_client():
    return APIClient()


def _make_user(account_type, email):
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type=account_type,
    )


def _make_business(email, name="Sweep Biz"):
    user = _make_user("business", email)
    return BusinessProfile.objects.create(
        user=user,
        business_name=name,
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )


def _call(client, method, url, payload=None):
    if method == "get":
        return client.get(url)
    return getattr(client, method)(url, payload or {}, format="json")


_AUTH_REQUIRED = [
    ("get", "businesses:business-me", None),
    ("post", "businesses:business-me", _BUSINESS_PAYLOAD),
    ("patch", "businesses:business-me", {"city": "Giza"}),
    ("get", "customers:customer-me", None),
    ("post", "customers:customer-me", _CUSTOMER_PAYLOAD),
    ("patch", "customers:customer-me", {"city": "Giza"}),
]


@pytest.mark.parametrize(
    "method,url_name,payload",
    _AUTH_REQUIRED,
    ids=[f"{m}-{n}" for m, n, _ in _AUTH_REQUIRED],
)
def test_unauthenticated_gets_401_envelope_and_creates_nothing(
    api_client, method, url_name, payload
):
    response = _call(api_client, method, reverse(url_name), payload)

    assert_unauthenticated(response)
    assert BusinessProfile.objects.count() == 0
    assert CustomerProfile.objects.count() == 0


def test_public_business_profile_needs_no_auth_and_unknown_id_is_404(api_client):
    profile = _make_business("p096-pub@example.com")

    ok = api_client.get(
        reverse("businesses:business-public", kwargs={"pk": profile.id})
    )
    missing = api_client.get(
        reverse("businesses:business-public", kwargs={"pk": 999999})
    )

    assert ok.status_code == 200
    assert missing.status_code == 404


def test_customer_account_creating_business_profile_is_rejected_with_envelope(
    api_client,
):
    user = _make_user("customer", "p096-cust-guard@example.com")
    api_client.force_authenticate(user=user)

    response = api_client.post(
        reverse("businesses:business-me"), _BUSINESS_PAYLOAD, format="json"
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert BusinessProfile.objects.count() == 0


def test_business_account_creating_customer_profile_is_rejected_with_envelope(
    api_client,
):
    user = _make_user("business", "p096-biz-guard@example.com")
    api_client.force_authenticate(user=user)

    response = api_client.post(
        reverse("customers:customer-me"), _CUSTOMER_PAYLOAD, format="json"
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert CustomerProfile.objects.count() == 0


def test_business_patch_with_spoofed_ids_never_touches_another_business(
    api_client,
):
    victim = _make_business("p096-victim@example.com", name="Victim Biz")
    attacker = _make_business("p096-attacker@example.com", name="Attacker Biz")
    api_client.force_authenticate(user=attacker.user)

    response = api_client.patch(
        reverse("businesses:business-me"),
        {"id": victim.id, "user": victim.user_id, "business_name": "Renamed"},
        format="json",
    )

    assert response.status_code == 200
    victim.refresh_from_db()
    attacker.refresh_from_db()
    assert victim.business_name == "Victim Biz"
    assert victim.user_id != attacker.user_id
    assert attacker.business_name == "Renamed"


def test_owner_cannot_self_grant_featured_verified_or_followers(api_client):
    profile = _make_business("p096-priv@example.com")
    api_client.force_authenticate(user=profile.user)

    response = api_client.patch(
        reverse("businesses:business-me"),
        {
            "is_featured": True,
            "is_verified": True,
            "follower_count": 9999,
            "city": "Giza",
        },
        format="json",
    )

    assert response.status_code == 200
    profile.refresh_from_db()
    assert profile.is_featured is False
    assert profile.follower_count == 0
    assert profile.city == "Giza"
    assert response.json()["is_verified"] is False


def test_customer_me_resolves_from_request_user_never_from_another_customer(
    api_client,
):
    first = _make_user("customer", "p096-c1@example.com")
    second = _make_user("customer", "p096-c2@example.com")
    CustomerProfile.objects.create(
        user=first, display_name="First", country="Egypt", city="Cairo"
    )
    CustomerProfile.objects.create(
        user=second, display_name="Second", country="Egypt", city="Cairo"
    )
    api_client.force_authenticate(user=second)

    response = api_client.get(reverse("customers:customer-me"))
    patched = api_client.patch(
        reverse("customers:customer-me"),
        {"display_name": "Second Renamed", "user": first.id},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "Second"
    assert patched.status_code == 200
    assert CustomerProfile.objects.get(user=first).display_name == "First"
    assert CustomerProfile.objects.get(user=second).display_name == "Second Renamed"
