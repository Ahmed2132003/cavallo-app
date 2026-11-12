"""
Tests for PATCH /api/v1/auth/me/profile/ and the new GET /me/ fields
(Part P-116A-1).

Storage: the default storage is swapped for Django's InMemoryStorage for
the duration of each test (autouse fixture below), so these tests need no
MinIO/S3. Django resets default_storage on a STORAGES setting change.
"""

import struct

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

User = get_user_model()

pytestmark = pytest.mark.django_db

ME_URL = "/api/v1/auth/me/"
PROFILE_URL = "/api/v1/auth/me/profile/"

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    + struct.pack(">I", 13)
    + b"IHDR"
    + struct.pack(">IIBBBBB", 10, 10, 8, 6, 0, 0, 0)
    + b"0000"
    + b"0" * 100
)
JPEG_BYTES = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    + b"\xff\xdb"
    + b"\x00" * 100
)
WEBP_BYTES = b"RIFF" + struct.pack("<I", 36) + b"WEBPVP8 " + b"\x00" * 40
EXE_BYTES = (
    b"MZ" + b"\x90\x00" * 30 + b"This program cannot be run in DOS mode" + b"0" * 200
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


def _make_user(role):
    kwargs = {}
    if role == "business":
        kwargs["account_type"] = User.ACCOUNT_TYPE_BUSINESS
    if role == "moderator":
        kwargs["is_moderator"] = True
    if role == "staff":
        kwargs["is_staff"] = True
    email = f"{role}@example.com"
    return User.objects.create_user(
        username=email, email=email, password="StrongPass123!", **kwargs
    )


@pytest.fixture(params=["customer", "business", "moderator", "staff"])
def user(request):
    # Deliberately NO CustomerProfile / BusinessProfile for any role.
    return _make_user(request.param)


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


def _png(name="a.png"):
    return SimpleUploadedFile(name, PNG_BYTES, content_type="image/png")


def _upload(client, upload):
    return client.patch(PROFILE_URL, {"avatar": upload}, format="multipart")


def test_upload_ok_and_visible_in_me(client, user):
    response = _upload(client, _png())

    assert response.status_code == 200
    body = response.json()
    assert body["avatar"].startswith("http")
    assert body["avatar"].endswith(".png") or ".png" in body["avatar"]

    user.refresh_from_db()
    assert user.avatar and default_storage.exists(user.avatar.name)
    assert user.avatar.name.startswith("avatars/")

    me = client.get(ME_URL).json()
    assert me["avatar"] == body["avatar"]
    assert me["display_name"] == ""


@pytest.mark.parametrize(
    "data,ext", [(JPEG_BYTES, "jpg"), (WEBP_BYTES, "webp"), (PNG_BYTES, "png")]
)
def test_allowed_types_get_server_side_name(client, user, data, ext):
    upload = SimpleUploadedFile("../../evil.html", data)

    response = _upload(client, upload)

    assert response.status_code == 200, response.json()
    user.refresh_from_db()
    assert user.avatar.name.startswith("avatars/")
    assert user.avatar.name.endswith("." + ext)
    assert "evil" not in user.avatar.name


def test_replace_deletes_old_file(client, user):
    _upload(client, _png("one.png"))
    user.refresh_from_db()
    old_name = user.avatar.name
    assert default_storage.exists(old_name)

    response = _upload(client, _png("two.png"))

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.avatar.name != old_name
    assert default_storage.exists(user.avatar.name)
    assert not default_storage.exists(old_name)


def test_remove_avatar(client, user):
    _upload(client, _png())
    user.refresh_from_db()
    old_name = user.avatar.name

    response = client.patch(PROFILE_URL, {"remove_avatar": True}, format="json")

    assert response.status_code == 200
    assert response.json()["avatar"] is None
    assert client.get(ME_URL).json()["avatar"] is None
    user.refresh_from_db()
    assert not user.avatar
    assert not default_storage.exists(old_name)


def test_display_name_update(client, user):
    response = client.patch(PROFILE_URL, {"display_name": "  Sara K  "}, format="json")

    assert response.status_code == 200
    assert response.json()["display_name"] == "Sara K"
    user.refresh_from_db()
    assert user.display_name == "Sara K"
    assert client.get(ME_URL).json()["display_name"] == "Sara K"


def test_display_name_80_ok_81_rejected(client, user):
    ok = client.patch(PROFILE_URL, {"display_name": "a" * 80}, format="json")
    assert ok.status_code == 200

    bad = client.patch(PROFILE_URL, {"display_name": "b" * 81}, format="json")

    assert bad.status_code == 400
    error = bad.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "display_name" in error["fields"]
    user.refresh_from_db()
    assert user.display_name == "a" * 80


def test_fake_exe_renamed_jpg_rejected_and_db_unchanged(client, user):
    user.display_name = "Before"
    user.save(update_fields=["display_name"])
    fake = SimpleUploadedFile("avatar.jpg", EXE_BYTES, content_type="image/jpeg")

    response = client.patch(
        PROFILE_URL, {"avatar": fake, "display_name": "After"}, format="multipart"
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "avatar" in error["fields"]
    user.refresh_from_db()
    assert not user.avatar
    assert user.display_name == "Before"


def test_file_over_5mb_rejected(client, user):
    big = SimpleUploadedFile(
        "big.png", PNG_BYTES + b"0" * (5 * 1024 * 1024), content_type="image/png"
    )

    response = _upload(client, big)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "avatar" in response.json()["error"]["fields"]
    user.refresh_from_db()
    assert not user.avatar


def test_invalid_upload_keeps_existing_avatar(client, user):
    _upload(client, _png())
    user.refresh_from_db()
    kept = user.avatar.name

    fake = SimpleUploadedFile("x.png", EXE_BYTES, content_type="image/png")
    response = _upload(client, fake)

    assert response.status_code == 400
    user.refresh_from_db()
    assert user.avatar.name == kept
    assert default_storage.exists(kept)


def test_no_token_returns_401():
    response = APIClient().patch(PROFILE_URL, {"display_name": "x"}, format="json")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_FAILED"


@pytest.mark.parametrize("payload", [{}, {"remove_avatar": False}, {"is_staff": True}])
def test_empty_request_is_400(client, user, payload):
    response = client.patch(PROFILE_URL, payload, format="json")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_empty_multipart_request_is_400(client):
    response = client.patch(PROFILE_URL, {}, format="multipart")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_avatar_and_remove_avatar_together_is_400(client, user):
    response = client.patch(
        PROFILE_URL,
        {"avatar": _png(), "remove_avatar": True},
        format="multipart",
    )

    assert response.status_code == 400
    assert "remove_avatar" in response.json()["error"]["fields"]
    user.refresh_from_db()
    assert not user.avatar


def test_privileged_fields_in_body_are_ignored(client, user):
    before = {
        "is_staff": user.is_staff,
        "is_moderator": user.is_moderator,
        "is_superuser": user.is_superuser,
        "account_type": user.account_type,
        "email": user.email,
        "preferred_language": user.preferred_language,
    }
    other = User.objects.create_user(username="o@e.com", email="o@e.com", password="x")

    response = client.patch(
        PROFILE_URL,
        {
            "display_name": "Legit",
            "id": other.id,
            "is_staff": not before["is_staff"],
            "is_moderator": not before["is_moderator"],
            "is_superuser": True,
            "account_type": (
                "customer" if before["account_type"] == "business" else "business"
            ),
            "email": "hacker@example.com",
            "preferred_language": "en",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["id"] == user.id
    user.refresh_from_db()
    assert user.display_name == "Legit"
    for field, value in before.items():
        assert getattr(user, field) == value, field
    other.refresh_from_db()
    assert other.display_name == ""


def test_other_users_avatar_is_never_touched(client, user):
    other = User.objects.create_user(username="o@e.com", email="o@e.com", password="x")
    other.avatar.save("keep.png", ContentFile(PNG_BYTES))
    other_name = other.avatar.name

    _upload(client, _png())
    client.patch(PROFILE_URL, {"remove_avatar": True}, format="json")

    other.refresh_from_db()
    assert other.avatar.name == other_name
    assert default_storage.exists(other_name)


def test_staff_without_any_profile_works():
    staff = _make_user("staff")
    api_client = APIClient()
    api_client.force_authenticate(user=staff)

    response = api_client.patch(
        PROFILE_URL,
        {"display_name": "Staffer", "avatar": _png()},
        format="multipart",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Staffer"
    assert body["avatar"] is not None
    assert body["is_staff"] is True


def test_get_on_profile_endpoint_not_allowed(client):
    response = client.get(PROFILE_URL)

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_display_name_written_through_to_existing_customer_profile():
    # Assumption (businesses/ is outside the bundle): CustomerProfile has a
    # `user` FK/1:1 and a `display_name` field, and no other required fields.
    from businesses.models import CustomerProfile

    customer = _make_user("customer")
    CustomerProfile.objects.create(user=customer, display_name="Old")
    api_client = APIClient()
    api_client.force_authenticate(user=customer)

    response = api_client.patch(PROFILE_URL, {"display_name": "New"}, format="json")

    assert response.status_code == 200
    assert CustomerProfile.objects.get(user=customer).display_name == "New"
