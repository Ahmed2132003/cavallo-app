"""
Tests for the commenter's public info on CommentSerializer (Part P-116A-2b).

``user`` (a bare id, the P-058 gap) is unchanged. ``user_public`` is added
next to it and comes from accounts.serializers.UserPublicSerializer.

Storage: the default storage is swapped for Django's InMemoryStorage for
each test (same approach as accounts/tests/test_user_public_serializer.py),
so no MinIO/S3 is needed.
"""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from businesses.models import CustomerProfile
from core.tests.sweep_factories import client_for, make_business, make_post, make_user
from social.models import Comment

pytestmark = pytest.mark.django_db

OLD_KEYS = {
    "id",
    "user",
    "content_type",
    "object_id",
    "text",
    "is_hidden",
    "created_at",
}
FORBIDDEN_KEYS = (
    "email",
    "username",
    "phone",
    "is_staff",
    "is_moderator",
    "account_type",
)


@pytest.fixture(autouse=True)
def _in_memory_storage(settings):
    storages = dict(settings.STORAGES)
    storages["default"] = {
        "BACKEND": "django.core.files.storage.InMemoryStorage",
        "OPTIONS": {"base_url": "/media/"},
    }
    settings.STORAGES = storages
    yield


def _post():
    return make_post(make_business())


def _commenter(display_name="", avatar=False):
    user = make_user()
    user.display_name = display_name
    user.save()
    if avatar:
        user.avatar.save("face.png", ContentFile(b"not-really-a-png"), save=True)
    return user


def _comment(user, post, text="hello"):
    return Comment.objects.create(
        user=user,
        content_type=ContentType.objects.get_for_model(type(post)),
        object_id=post.pk,
        text=text,
    )


def _list(client, post):
    return client.get(
        reverse("comments:collection"),
        {"content_type": "post", "object_id": post.pk},
    )


def _only_item(response):
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    return results[0]


class TestCommentUserPublic:
    def test_old_keys_kept_and_user_public_added(self):
        post = _post()
        author = _commenter("Mona")
        _comment(author, post)

        item = _only_item(_list(client_for(), post))

        assert set(item.keys()) == OLD_KEYS | {"user_public"}
        assert item["user"] == author.pk
        assert set(item["user_public"].keys()) == {"display_name", "avatar"}

    def test_shows_display_name_and_avatar_url(self):
        post = _post()
        _comment(_commenter("Mona", avatar=True), post)

        item = _only_item(_list(client_for(), post))

        assert item["user_public"]["display_name"] == "Mona"
        assert item["user_public"]["avatar"].startswith(
            "http://testserver/media/avatars/"
        )

    def test_avatar_is_null_when_commenter_has_none(self):
        post = _post()
        _comment(_commenter("Mona"), post)

        item = _only_item(_list(client_for(), post))

        assert item["user_public"] == {"display_name": "Mona", "avatar": None}

    def test_no_identity_or_role_fields_in_new_output(self):
        post = _post()
        author = _commenter("Mona", avatar=True)
        _comment(author, post)

        response = _list(client_for(), post)
        item = _only_item(response)

        assert not set(FORBIDDEN_KEYS) & set(item["user_public"].keys())
        text = response.content.decode()
        for key in FORBIDDEN_KEYS:
            assert f'"{key}"' not in text
        assert author.email not in text
        assert author.username not in text

    def test_staff_and_moderator_commenters_without_profile_work(self):
        post = _post()
        staff = make_user()
        staff.is_staff = True
        staff.save()
        moderator = make_user()
        moderator.is_moderator = True
        moderator.save()
        for user in (staff, moderator):
            CustomerProfile.objects.filter(user=user).delete()
            _comment(user, post)

        response = _list(client_for(), post)

        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 2
        for item in results:
            assert item["user_public"] == {"display_name": "", "avatar": None}

    def test_create_response_also_carries_user_public_with_avatar_url(self):
        post = _post()
        author = _commenter("Mona", avatar=True)

        response = client_for(author).post(
            reverse("comments:collection"),
            {"content_type": "post", "object_id": post.pk, "text": "nice"},
            format="json",
        )

        assert response.status_code == 201
        body = response.json()
        assert body["user"] == author.pk
        assert body["user_public"]["display_name"] == "Mona"
        assert body["user_public"]["avatar"].startswith(
            "http://testserver/media/avatars/"
        )

    def test_list_query_count_does_not_grow_with_commenters(self):
        post = _post()
        client = client_for()
        _comment(_commenter("One", avatar=True), post)
        _list(client, post)  # warm-up (ContentType cache etc.)

        with CaptureQueriesContext(connection) as one_commenter:
            assert _list(client, post).status_code == 200

        # Five more commenters, one of every profile shape.
        _comment(_commenter("Two", avatar=True), post)
        business_owner = make_business("Nile Textiles").user
        _comment(business_owner, post)
        customer = _commenter()
        CustomerProfile.objects.create(user=customer, display_name="Mona Ali")
        _comment(customer, post)
        _comment(_commenter(), post)
        staff = _commenter()
        staff.is_staff = True
        staff.save()
        _comment(staff, post)

        with CaptureQueriesContext(connection) as six_commenters:
            response = _list(client, post)

        assert response.status_code == 200
        assert len(response.json()["results"]) == 6
        assert len(six_commenters) == len(one_commenter)
