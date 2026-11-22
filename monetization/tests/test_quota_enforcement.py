"""BE-05: storage quota enforcement on every merchant upload path."""

from pathlib import Path
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from businesses.services import create_business_profile
from categories.models import Category
from content.models import Post, Reel
from core.exceptions import StorageQuotaExceeded
from core.tests.test_media import _VALID_PNG_BYTES
from decimal import Decimal
from monetization.models import Plan, StoredFile, Subscription
from monetization.quota import ensure_quota_available
from monetization.services import create_free_subscription
from monetization.storage_usage import get_used_bytes
from products.models import Product
from stories.models import Story

User = get_user_model()
pytestmark = pytest.mark.django_db

PNG = _VALID_PNG_BYTES
PNG_SIZE = len(PNG)
MP4 = (
    Path(__file__).resolve().parents[2] / "content/tests/fixtures/small_test_reel.mp4"
).read_bytes()
KINDS = ["product", "post", "reel", "story"]
MESSAGE_AR = "ترقية"


@pytest.fixture(autouse=True)
def _no_transcode():
    with mock.patch("content.views.transcode_reel.delay"):
        yield


def _business(suffix="a", quota=None):
    email = f"quota-{suffix}@example.com"
    user = User.objects.create_user(
        username=email, email=email, password="testpass123", account_type="business"
    )
    business = create_business_profile(
        user=user,
        business_name=f"Quota Trader {suffix}",
        business_type="trader",
        country="EG",
        city="Ismailia",
    )
    Plan.objects.get_or_create(
        code="free",
        defaults={
            "name": "Free",
            "duration_days": 30,
            "price": Decimal("0.00"),
            "currency": "EGP",
            "trial_days": 15,
        },
    )
    create_free_subscription(business)
    if quota is not None:
        Subscription.objects.filter(business_id=business.pk).update(quota_bytes=quota)
    return business


def _client(business):
    client = APIClient()
    client.force_authenticate(user=business.user)
    return client


def _png(name="f.png"):
    return SimpleUploadedFile(name, PNG, content_type="image/png")


def _spec(kind):
    """(url, multipart data, size of the new file, model, storage prefix)."""
    if kind == "product":
        category = Category.objects.get_or_create(name="Fashion")[0]
        data = {
            "category": category.id,
            "name": "P",
            "price": "10.00",
            "currency": "EGP",
            "image": _png("p.png"),
        }
        return "/api/v1/products/", data, PNG_SIZE, Product, "products/"
    if kind == "post":
        return (
            "/api/v1/posts/",
            {"caption": "c", "image": _png("post.png")},
            PNG_SIZE,
            Post,
            "posts/",
        )
    if kind == "reel":
        video = SimpleUploadedFile("r.mp4", MP4, content_type="video/mp4")
        return (
            "/api/v1/reels/",
            {"caption": "c", "video": video},
            len(MP4),
            Reel,
            "reels/videos/",
        )
    return (
        "/api/v1/stories/",
        {"media": _png("s.png")},
        PNG_SIZE,
        Story,
        "stories/media/",
    )


def _listing(prefix):
    try:
        return set(default_storage.listdir(prefix)[1])
    except Exception:
        return set()


class TestEnsureQuotaAvailable:
    def test_within_quota_passes_and_exact_fit_is_allowed(self):
        business = _business(quota=1000)

        ensure_quota_available(business, 1000)

    def test_over_quota_raises_with_payload(self):
        business = _business(quota=1000)

        with pytest.raises(StorageQuotaExceeded) as exc_info:
            ensure_quota_available(business, 1001)

        assert exc_info.value.status_code == 403
        assert exc_info.value.extra_details == {
            "used_bytes": 0,
            "quota_bytes": 1000,
            "incoming_bytes": 1001,
            "upgrade_required": True,
        }

    def test_zero_or_negative_incoming_is_always_allowed(self):
        business = _business(quota=10)
        Post.objects.create(
            business=business, caption="c", image=SimpleUploadedFile("o.png", b"x" * 500)
        )
        assert get_used_bytes(business) > 10

        ensure_quota_available(business, 0)
        ensure_quota_available(business, -300)

    def test_business_without_subscription_is_never_blocked(self):
        business = _business()
        Subscription.objects.filter(business_id=business.pk).delete()

        ensure_quota_available(business, 10**15)


class TestCreateEnforcement:
    @pytest.mark.parametrize("kind", KINDS)
    def test_under_the_limit_succeeds_and_counts(self, kind):
        url, data, size, model, _prefix = _spec(kind)
        business = _business(quota=size)  # exact fit

        response = _client(business).post(url, data, format="multipart")

        assert response.status_code == 201, response.content
        assert get_used_bytes(business) >= size
        assert model.all_objects.count() == 1

    @pytest.mark.parametrize("kind", KINDS)
    def test_over_the_limit_is_403_and_stores_nothing(self, kind):
        url, data, size, model, prefix = _spec(kind)
        business = _business(quota=size - 1)
        before = _listing(prefix)

        response = _client(business).post(url, data, format="multipart")

        assert response.status_code == 403
        error = response.json()["error"]
        assert error["code"] == "STORAGE_QUOTA_EXCEEDED"
        assert error["details"] == {
            "used_bytes": 0,
            "quota_bytes": size - 1,
            "incoming_bytes": size,
            "upgrade_required": True,
        }
        assert model.all_objects.count() == 0
        assert StoredFile.objects.count() == 0
        assert _listing(prefix) == before

    def test_business_without_subscription_can_upload(self):
        business = _business()
        Subscription.objects.filter(business_id=business.pk).delete()
        url, data, _size, _model, _prefix = _spec("post")

        response = _client(business).post(url, data, format="multipart")

        assert response.status_code == 201

    def test_post_without_a_file_is_not_checked(self):
        business = _business(quota=0)

        response = _client(business).post(
            "/api/v1/posts/", {"caption": "text only"}, format="json"
        )

        assert response.status_code == 201

    def test_other_business_usage_does_not_count(self):
        other = _business("other")
        Post.objects.create(
            business=other, caption="c", image=SimpleUploadedFile("o.png", b"x" * 5000)
        )
        business = _business("mine", quota=PNG_SIZE)
        url, data, _size, _model, _prefix = _spec("post")

        response = _client(business).post(url, data, format="multipart")

        assert response.status_code == 201


class TestReplacement:
    def _post(self, business, size):
        return Post.objects.create(
            business=business,
            caption="c",
            image=SimpleUploadedFile("old.png", b"x" * size),
        )

    def test_only_the_net_difference_is_counted(self):
        # used 100, new 133 -> net +33; quota 133 fits only if net counts.
        business = _business(quota=PNG_SIZE)
        post = self._post(business, 100)

        response = _client(business).patch(
            f"/api/v1/posts/{post.id}/", {"image": _png()}, format="multipart"
        )

        assert response.status_code == 200, response.content
        assert get_used_bytes(business) == PNG_SIZE

    def test_net_increase_over_the_limit_is_rejected(self):
        business = _business(quota=PNG_SIZE - 1)
        post = self._post(business, 100)

        response = _client(business).patch(
            f"/api/v1/posts/{post.id}/", {"image": _png()}, format="multipart"
        )

        assert response.status_code == 403
        assert response.json()["error"]["details"]["incoming_bytes"] == PNG_SIZE - 100
        assert get_used_bytes(business) == 100

    def test_smaller_replacement_is_allowed_while_over_the_limit(self):
        business = _business(quota=500)
        post = self._post(business, 2000)  # already over quota

        response = _client(business).patch(
            f"/api/v1/posts/{post.id}/", {"image": _png()}, format="multipart"
        )

        assert response.status_code == 200
        assert get_used_bytes(business) == PNG_SIZE

    def test_editing_text_while_over_the_limit_is_allowed(self):
        business = _business(quota=10)
        post = self._post(business, 2000)

        response = _client(business).patch(
            f"/api/v1/posts/{post.id}/", {"caption": "edited"}, format="json"
        )

        assert response.status_code == 200

    def test_product_replacement_is_checked(self):
        business = _business(quota=PNG_SIZE - 1)
        category = Category.objects.get_or_create(name="Fashion")[0]
        product = Product.objects.create(
            business=business,
            category=category,
            name="P",
            price="10.00",
            currency="EGP",
            image=SimpleUploadedFile("old.png", b"x" * 100),
        )

        response = _client(business).patch(
            f"/api/v1/products/{product.id}/", {"image": _png()}, format="multipart"
        )

        assert response.status_code == 403


class TestDelete:
    def test_delete_is_allowed_when_over_quota_and_frees_space(self):
        business = _business(quota=500)
        post = Post.objects.create(
            business=business, caption="c", image=SimpleUploadedFile("o.png", b"x" * 2000)
        )
        client = _client(business)
        url, data, _size, _model, _prefix = _spec("post")
        assert client.post(url, data, format="multipart").status_code == 403

        assert client.delete(f"/api/v1/posts/{post.id}/").status_code == 204

        assert get_used_bytes(business) == 0
        _url, data, _size, _model, _prefix = _spec("post")
        assert client.post(url, data, format="multipart").status_code == 201


class TestTranslation:
    def _over(self, business, **headers):
        url, data, _size, _model, _prefix = _spec("post")
        return _client(business).post(url, data, format="multipart", **headers)

    def test_arabic_message(self):
        response = self._over(_business(quota=1), HTTP_ACCEPT_LANGUAGE="ar")

        assert response.status_code == 403
        assert MESSAGE_AR in response.json()["error"]["message"]

    def test_english_message(self):
        response = self._over(_business(quota=1), HTTP_ACCEPT_LANGUAGE="en")

        assert "Upgrade your plan" in response.json()["error"]["message"]