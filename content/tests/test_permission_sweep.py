"""
P-096 (step 1) permission / IDOR sweep for the content app (Posts and
Reels share one parametrised suite, since their views are
byte-for-byte the same ownership pattern).

For both kinds: anonymous write -> 401, a different business or a
customer account -> 403, an authenticated write on an unknown id -> 404;
every denied request asserts the P-012 envelope AND re-reads the row to
prove nothing changed (caption, soft-delete flag, row count).
"""

import pytest
from rest_framework.test import APIClient

from accounts.models import User
from businesses.services import create_business_profile
from content.models import Post, Reel
from content.tests.test_api import _make_video_upload
from core.tests.sweep_helpers import (
    assert_forbidden,
    assert_not_found,
    assert_unauthenticated,
)

pytestmark = pytest.mark.django_db

KINDS = ["post", "reel"]


def _make_business_user(email, name):
    user = User.objects.create_user(
        username=email, email=email, password="testpass123", account_type="business"
    )
    business = create_business_profile(
        user=user,
        business_name=name,
        business_type="trader",
        country="EG",
        city="Ismailia",
    )
    return user, business


def _make_customer(email):
    return User.objects.create_user(
        username=email, email=email, password="testpass123", account_type="customer"
    )


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def world():
    owner, business = _make_business_user("p096-content-owner@example.com", "Owner")
    post = Post.objects.create(business=business, caption="original caption")
    reel = Reel.objects.create(
        business=business, caption="original caption", video=_make_video_upload()
    )
    return {"owner": owner, "business": business, "post": post, "reel": reel}


def _model(kind):
    return Post if kind == "post" else Reel


def _list_url(kind):
    return f"/api/v1/{kind}s/"


def _detail_url(kind, pk):
    return f"/api/v1/{kind}s/{pk}/"


def _count(kind):
    return _model(kind)._base_manager.count()


def _assert_untouched(obj):
    obj.refresh_from_db()
    assert obj.caption == "original caption"
    assert obj.is_deleted is False


def _create(client, kind):
    if kind == "post":
        return client.post(_list_url(kind), {"caption": "ghost"}, format="json")
    return client.post(
        _list_url(kind),
        {"caption": "ghost", "video": _make_video_upload()},
        format="multipart",
    )


@pytest.mark.parametrize("method", ["patch", "delete"])
@pytest.mark.parametrize("kind", KINDS)
def test_unauthenticated_write_gets_401_and_changes_nothing(
    api_client, world, kind, method
):
    obj = world[kind]
    url = _detail_url(kind, obj.pk)

    if method == "patch":
        response = api_client.patch(url, {"caption": "hijacked"}, format="json")
    else:
        response = api_client.delete(url)

    assert_unauthenticated(response)
    _assert_untouched(obj)


@pytest.mark.parametrize("kind", KINDS)
def test_unauthenticated_create_and_own_list_get_401_and_create_nothing(
    api_client, world, kind
):
    before = _count(kind)

    created = _create(api_client, kind)
    listed = api_client.get(_list_url(kind))

    assert_unauthenticated(created)
    assert_unauthenticated(listed)
    assert _count(kind) == before


@pytest.mark.parametrize("actor", ["other_business", "customer"])
@pytest.mark.parametrize("method", ["patch", "delete"])
@pytest.mark.parametrize("kind", KINDS)
def test_non_owner_write_gets_403_and_changes_nothing(
    api_client, world, kind, method, actor
):
    if actor == "other_business":
        user, _ = _make_business_user("p096-content-other@example.com", "Other")
    else:
        user = _make_customer("p096-content-customer@example.com")
    api_client.force_authenticate(user)
    obj = world[kind]
    url = _detail_url(kind, obj.pk)

    if method == "patch":
        response = api_client.patch(url, {"caption": "hijacked"}, format="json")
    else:
        response = api_client.delete(url)

    assert_forbidden(response)
    _assert_untouched(obj)


@pytest.mark.parametrize("kind", KINDS)
def test_customer_without_business_profile_cannot_create(api_client, world, kind):
    api_client.force_authenticate(_make_customer("p096-content-nobiz@example.com"))
    before = _count(kind)

    response = _create(api_client, kind)

    assert_forbidden(response)
    assert _count(kind) == before


@pytest.mark.parametrize("method", ["patch", "delete"])
@pytest.mark.parametrize("kind", KINDS)
def test_authenticated_write_on_unknown_id_is_404_with_envelope(
    api_client, world, kind, method
):
    api_client.force_authenticate(world["owner"])
    url = _detail_url(kind, 999999)

    if method == "patch":
        response = api_client.patch(url, {"caption": "x"}, format="json")
    else:
        response = api_client.delete(url)

    assert_not_found(response)


@pytest.mark.parametrize("kind", KINDS)
def test_public_reads_stay_open_to_anonymous_users(api_client, world, kind):
    detail = api_client.get(_detail_url(kind, world[kind].pk))
    public_list = api_client.get(f"{_list_url(kind)}public/")

    assert detail.status_code == 200
    assert public_list.status_code == 200
