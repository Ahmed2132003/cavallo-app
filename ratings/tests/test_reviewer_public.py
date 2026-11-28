"""
Part P-116A-2c - reviewer display_name + avatar in the public ratings list.

Checks: the new ``reviewer`` key (display_name + avatar URL / null), no
leaked account fields, every old key still present, a reviewer with no
CustomerProfile still works, and the list's query count does not grow
with the number of ratings.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from core.tests.sweep_factories import make_business, make_user
from ratings.models import Rating

pytestmark = pytest.mark.django_db

OLD_KEYS = {"id", "customer", "score", "review_text", "created_at"}
FORBIDDEN_KEYS = {
    "email",
    "username",
    "phone",
    "is_staff",
    "is_moderator",
    "account_type",
}


def _url(business_pk):
    return reverse("ratings:business-ratings", kwargs={"pk": business_pk})


def _results(response):
    assert response.status_code == 200
    data = response.json()
    return data["results"] if isinstance(data, dict) else data


def _rate(business, user, score=5):
    return Rating.objects.create(
        customer=user, business=business, score=score, review_text="nice"
    )


def test_reviewer_shows_display_name_and_avatar_url():
    business = make_business()
    reviewer = make_user("customer", "p116a2c-named")
    User.objects.filter(pk=reviewer.pk).update(
        display_name="Sara A", avatar="avatars/sara.png"
    )
    _rate(business, reviewer)

    rows = _results(APIClient().get(_url(business.pk)))

    assert len(rows) == 1
    assert rows[0]["reviewer"]["display_name"] == "Sara A"
    avatar = rows[0]["reviewer"]["avatar"]
    assert avatar.startswith("http")
    assert "avatars/sara.png" in avatar


def test_reviewer_avatar_is_null_when_user_has_no_avatar():
    business = make_business()
    reviewer = make_user("customer", "p116a2c-noavatar")
    User.objects.filter(pk=reviewer.pk).update(display_name="No Pic")
    _rate(business, reviewer)

    rows = _results(APIClient().get(_url(business.pk)))

    assert rows[0]["reviewer"]["display_name"] == "No Pic"
    assert rows[0]["reviewer"]["avatar"] is None


def test_reviewer_has_only_two_keys_and_nothing_leaks():
    business = make_business()
    reviewer = make_user("customer", "p116a2c-leak")
    User.objects.filter(pk=reviewer.pk).update(display_name="Leak Check")
    _rate(business, reviewer)

    response = APIClient().get(_url(business.pk))
    rows = _results(response)

    assert set(rows[0]["reviewer"].keys()) == {"display_name", "avatar"}
    assert FORBIDDEN_KEYS.isdisjoint(rows[0].keys())
    body = response.content.decode()
    assert reviewer.email not in body
    assert reviewer.username not in body


def test_old_keys_are_still_present_and_unchanged():
    business = make_business()
    reviewer = make_user("customer", "p116a2c-oldkeys")
    rating = _rate(business, reviewer, score=4)

    rows = _results(APIClient().get(_url(business.pk)))

    assert OLD_KEYS.issubset(rows[0].keys())
    assert rows[0]["id"] == rating.pk
    assert rows[0]["customer"] == reviewer.pk
    assert rows[0]["score"] == 4
    assert rows[0]["review_text"] == "nice"


def test_reviewer_without_customer_profile_works():
    business = make_business()
    reviewer = make_user("customer", "p116a2c-noprofile")
    _rate(business, reviewer)

    rows = _results(APIClient().get(_url(business.pk)))

    reviewer_data = rows[0]["reviewer"]
    assert isinstance(reviewer_data["display_name"], str)
    assert reviewer_data["display_name"] not in (reviewer.email, reviewer.username)
    assert reviewer_data["avatar"] is None


def test_ratings_list_query_count_does_not_grow_with_rows():
    business = make_business()
    for i in range(2):
        user = make_user("customer", f"p116a2c-q-a{i}")
        User.objects.filter(pk=user.pk).update(display_name=f"Named {i}")
        _rate(business, user)

    client = APIClient()
    with CaptureQueriesContext(connection) as small:
        _results(client.get(_url(business.pk)))

    for i in range(6):
        user = make_user("customer", f"p116a2c-q-b{i}")
        # Mix: some with avatar/name, some with neither (profile fallback path).
        if i % 2 == 0:
            User.objects.filter(pk=user.pk).update(
                display_name=f"Extra {i}", avatar=f"avatars/extra{i}.png"
            )
        _rate(business, user)

    with CaptureQueriesContext(connection) as large:
        rows = _results(client.get(_url(business.pk)))

    assert len(rows) == 8
    assert len(large) == len(small)
