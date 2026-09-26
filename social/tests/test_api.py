"""
API tests for Part P-052 — FollowToggleView.

Mirrors businesses/tests/test_api.py's conventions (force_authenticate,
APIClient, pytest.mark.django_db). The concurrency test is the one
genuine exception: it needs @pytest.mark.django_db(transaction=True)
so two threads get real, separately-committed Postgres transactions
instead of sharing one wrapping test transaction — otherwise a race
condition could never actually manifest in the test.
"""

import inspect
import threading
from uuid import uuid4

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from businesses.models import BusinessProfile
from social.models import Follow
from django.core.files.uploadedfile import SimpleUploadedFile

pytestmark = pytest.mark.django_db


def _make_user(account_type: str, email: str) -> User:
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type=account_type,
    )


def _make_business() -> BusinessProfile:
    owner = _make_user("business", f"p052-owner-{uuid4().hex[:12]}@example.com")
    return BusinessProfile.objects.create(
        user=owner,
        business_name="Acme Trading",
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )


@pytest.fixture
def api_client():
    return APIClient()


def _follow_url(pk):
    return reverse("social:business-follow", kwargs={"pk": pk})


class TestFollowToggle:
    def test_follow_creates_row_and_increments_counters(self, api_client):
        follower = _make_user("customer", "p052-f1@example.com")
        business = _make_business()
        api_client.force_authenticate(user=follower)

        response = api_client.post(_follow_url(business.pk))

        assert response.status_code == 200
        assert response.json() == {"following": True}
        assert Follow.objects.filter(follower=follower, business=business).count() == 1
        business.refresh_from_db()
        follower.refresh_from_db()
        assert business.follower_count == 1
        assert follower.following_count == 1

    def test_follow_is_idempotent(self, api_client):
        follower = _make_user("customer", "p052-f2@example.com")
        business = _make_business()
        api_client.force_authenticate(user=follower)

        api_client.post(_follow_url(business.pk))
        response = api_client.post(_follow_url(business.pk))

        assert response.status_code == 200
        assert response.json() == {"following": True}
        assert Follow.objects.filter(follower=follower, business=business).count() == 1
        business.refresh_from_db()
        follower.refresh_from_db()
        assert business.follower_count == 1
        assert follower.following_count == 1

    def test_unfollow_decrements_counters(self, api_client):
        follower = _make_user("customer", "p052-f3@example.com")
        business = _make_business()
        api_client.force_authenticate(user=follower)
        api_client.post(_follow_url(business.pk))

        response = api_client.delete(_follow_url(business.pk))

        assert response.status_code == 200
        assert response.json() == {"following": False}
        assert Follow.objects.filter(follower=follower, business=business).count() == 0
        business.refresh_from_db()
        follower.refresh_from_db()
        assert business.follower_count == 0
        assert follower.following_count == 0

    def test_unfollow_never_followed_is_harmless_noop(self, api_client):
        follower = _make_user("customer", "p052-f4@example.com")
        business = _make_business()
        api_client.force_authenticate(user=follower)

        response = api_client.delete(_follow_url(business.pk))

        assert response.status_code == 200
        assert response.json() == {"following": False}
        assert Follow.objects.count() == 0
        business.refresh_from_db()
        assert business.follower_count == 0

    def test_unfollow_twice_does_not_double_decrement_or_go_negative(self, api_client):
        follower = _make_user("customer", "p052-f5@example.com")
        business = _make_business()
        api_client.force_authenticate(user=follower)
        api_client.post(_follow_url(business.pk))

        api_client.delete(_follow_url(business.pk))
        response = api_client.delete(_follow_url(business.pk))

        assert response.status_code == 200
        business.refresh_from_db()
        follower.refresh_from_db()
        assert business.follower_count == 0
        assert follower.following_count == 0

    def test_two_users_following_same_business_both_counted(self, api_client):
        business = _make_business()
        follower1 = _make_user("customer", "p052-f6@example.com")
        follower2 = _make_user("customer", "p052-f7@example.com")

        api_client.force_authenticate(user=follower1)
        api_client.post(_follow_url(business.pk))
        api_client.force_authenticate(user=follower2)
        api_client.post(_follow_url(business.pk))

        business.refresh_from_db()
        assert business.follower_count == 2
        assert Follow.objects.filter(business=business).count() == 2

    def test_unauthenticated_follow_returns_401(self, api_client):
        business = _make_business()
        response = api_client.post(_follow_url(business.pk))
        assert response.status_code == 401

    def test_unauthenticated_unfollow_returns_401(self, api_client):
        business = _make_business()
        response = api_client.delete(_follow_url(business.pk))
        assert response.status_code == 401

    def test_follow_nonexistent_business_returns_404(self, api_client):
        follower = _make_user("customer", "p052-f8@example.com")
        api_client.force_authenticate(user=follower)
        response = api_client.post(_follow_url(999999))
        assert response.status_code == 404

    def test_unfollow_nonexistent_business_returns_404(self, api_client):
        follower = _make_user("customer", "p052-f9@example.com")
        api_client.force_authenticate(user=follower)
        response = api_client.delete(_follow_url(999999))
        assert response.status_code == 404


@pytest.mark.django_db(transaction=True)
class TestFollowConcurrency:
    def test_concurrent_follow_requests_increment_exactly_once(self):
        """
        Two near-simultaneous POST /follow/ calls for the same
        user/business, fired from separate threads against real
        Postgres. Must result in exactly one Follow row and
        follower_count incremented by exactly 1, never 2 — proves
        get_or_create() + the F()-inside-transaction.atomic() pattern
        is actually race-condition-safe, not just correct when the
        two calls happen to run sequentially.
        """
        follower = _make_user("customer", "p052-conc1@example.com")
        business = _make_business()
        results = []

        def _do_follow():
            client = APIClient()
            client.force_authenticate(user=follower)
            resp = client.post(_follow_url(business.pk))
            results.append(resp.status_code)

        t1 = threading.Thread(target=_do_follow)
        t2 = threading.Thread(target=_do_follow)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results == [200, 200]
        assert Follow.objects.filter(follower=follower, business=business).count() == 1
        business.refresh_from_db()
        assert business.follower_count == 1


from django.contrib.contenttypes.models import ContentType

from content.models import Post, Reel
from social.models import Like


def _make_post(business=None):
    business = business or _make_business()
    return Post.objects.create(business=business, caption="hi")


def _make_reel(business=None):
    business = business or _make_business()
    return Reel.objects.create(
        business=business,
        caption="hi",
        video=SimpleUploadedFile(
            "raw.mp4",
            (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"),
            content_type="video/mp4",
        ),
    )


def _like_url():
    return reverse("likes:toggle")


class TestLikeTogglePost:
    def test_like_post_creates_row_and_increments_counter(self, api_client):
        liker = _make_user("customer", "p053-l1@example.com")
        post = _make_post()
        api_client.force_authenticate(user=liker)

        response = api_client.post(
            _like_url(), {"content_type": "post", "object_id": post.pk}, format="json"
        )

        assert response.status_code == 200
        assert response.json() == {"liked": True}
        post_ct = ContentType.objects.get_for_model(Post)
        assert (
            Like.objects.filter(
                user=liker, content_type=post_ct, object_id=post.pk
            ).count()
            == 1
        )
        post.refresh_from_db()
        assert post.likes_count == 1

    def test_like_post_is_idempotent(self, api_client):
        liker = _make_user("customer", "p053-l2@example.com")
        post = _make_post()
        api_client.force_authenticate(user=liker)

        api_client.post(
            _like_url(), {"content_type": "post", "object_id": post.pk}, format="json"
        )
        response = api_client.post(
            _like_url(), {"content_type": "post", "object_id": post.pk}, format="json"
        )

        assert response.status_code == 200
        post.refresh_from_db()
        assert post.likes_count == 1

    def test_unlike_post_decrements_counter(self, api_client):
        liker = _make_user("customer", "p053-l3@example.com")
        post = _make_post()
        api_client.force_authenticate(user=liker)
        api_client.post(
            _like_url(), {"content_type": "post", "object_id": post.pk}, format="json"
        )

        response = api_client.delete(
            _like_url(), {"content_type": "post", "object_id": post.pk}, format="json"
        )

        assert response.status_code == 200
        assert response.json() == {"liked": False}
        post.refresh_from_db()
        assert post.likes_count == 0

    def test_unlike_never_liked_is_harmless_noop(self, api_client):
        liker = _make_user("customer", "p053-l4@example.com")
        post = _make_post()
        api_client.force_authenticate(user=liker)

        response = api_client.delete(
            _like_url(), {"content_type": "post", "object_id": post.pk}, format="json"
        )

        assert response.status_code == 200
        post.refresh_from_db()
        assert post.likes_count == 0

    def test_unlike_twice_does_not_go_negative(self, api_client):
        liker = _make_user("customer", "p053-l5@example.com")
        post = _make_post()
        api_client.force_authenticate(user=liker)
        api_client.post(
            _like_url(), {"content_type": "post", "object_id": post.pk}, format="json"
        )

        api_client.delete(
            _like_url(), {"content_type": "post", "object_id": post.pk}, format="json"
        )
        response = api_client.delete(
            _like_url(), {"content_type": "post", "object_id": post.pk}, format="json"
        )

        assert response.status_code == 200
        post.refresh_from_db()
        assert post.likes_count == 0


class TestLikeToggleReel:
    def test_like_reel_creates_row_and_increments_counter(self, api_client):
        """
        Same mechanism proven against a different concrete model —
        the generic-FK dispatch works identically for Reel, not just
        Post, without any Reel-specific branch in the view.
        """
        liker = _make_user("customer", "p053-lr1@example.com")
        reel = _make_reel()
        api_client.force_authenticate(user=liker)

        response = api_client.post(
            _like_url(), {"content_type": "reel", "object_id": reel.pk}, format="json"
        )

        assert response.status_code == 200
        reel.refresh_from_db()
        assert reel.likes_count == 1

    def test_unlike_reel_decrements_counter(self, api_client):
        liker = _make_user("customer", "p053-lr2@example.com")
        reel = _make_reel()
        api_client.force_authenticate(user=liker)
        api_client.post(
            _like_url(), {"content_type": "reel", "object_id": reel.pk}, format="json"
        )

        response = api_client.delete(
            _like_url(), {"content_type": "reel", "object_id": reel.pk}, format="json"
        )

        assert response.status_code == 200
        reel.refresh_from_db()
        assert reel.likes_count == 0


class TestLikeToggleValidation:
    def test_unrecognized_content_type_returns_400(self, api_client):
        liker = _make_user("customer", "p053-v1@example.com")
        api_client.force_authenticate(user=liker)

        response = api_client.post(
            _like_url(), {"content_type": "story", "object_id": 1}, format="json"
        )

        assert response.status_code == 400

    def test_missing_object_id_returns_400(self, api_client):
        liker = _make_user("customer", "p053-v2@example.com")
        api_client.force_authenticate(user=liker)

        response = api_client.post(_like_url(), {"content_type": "post"}, format="json")

        assert response.status_code == 400

    def test_nonexistent_object_id_returns_404(self, api_client):
        liker = _make_user("customer", "p053-v3@example.com")
        api_client.force_authenticate(user=liker)

        response = api_client.post(
            _like_url(), {"content_type": "post", "object_id": 999999}, format="json"
        )

        assert response.status_code == 404

    def test_unauthenticated_like_returns_401(self, api_client):
        post = _make_post()
        response = api_client.post(
            _like_url(), {"content_type": "post", "object_id": post.pk}, format="json"
        )
        assert response.status_code == 401


@pytest.mark.django_db(transaction=True)
class TestLikeConcurrency:
    def test_concurrent_like_requests_increment_exactly_once(self):
        liker = _make_user("customer", "p053-conc1@example.com")
        post = _make_post()
        results = []

        def _do_like():
            client = APIClient()
            client.force_authenticate(user=liker)
            resp = client.post(
                _like_url(),
                {"content_type": "post", "object_id": post.pk},
                format="json",
            )
            results.append(resp.status_code)

        t1 = threading.Thread(target=_do_like)
        t2 = threading.Thread(target=_do_like)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results == [200, 200]
        post_ct = ContentType.objects.get_for_model(Post)
        assert (
            Like.objects.filter(
                user=liker, content_type=post_ct, object_id=post.pk
            ).count()
            == 1
        )
        post.refresh_from_db()
        assert post.likes_count == 1


from categories.models import Category
from products.models import Product

from social.models import Save


def _make_category(name="Fashion"):
    return Category.objects.create(name=name)


def _make_product(business=None, category=None, **overrides):
    defaults = {
        "business": business or _make_business(),
        "category": category or _make_category(),
        "name": "Classic Shirt",
        "description": "A shirt.",
        "price": "199.99",
        "currency": Product.CURRENCY_EGP,
    }
    defaults.update(overrides)
    return Product.objects.create(**defaults)


def _save_toggle_url():
    return reverse("saves:toggle")


def _save_list_url():
    return reverse("saves:list")


class TestSaveTogglePost:
    def test_save_post_creates_row(self, api_client):
        saver = _make_user("customer", "p054-s1@example.com")
        post = _make_post()
        api_client.force_authenticate(user=saver)

        response = api_client.post(
            _save_toggle_url(),
            {"content_type": "post", "object_id": post.pk},
            format="json",
        )

        assert response.status_code == 200
        assert response.json() == {"saved": True}
        post_ct = ContentType.objects.get_for_model(Post)
        assert (
            Save.objects.filter(
                user=saver, content_type=post_ct, object_id=post.pk
            ).count()
            == 1
        )

    def test_save_post_is_idempotent(self, api_client):
        saver = _make_user("customer", "p054-s2@example.com")
        post = _make_post()
        api_client.force_authenticate(user=saver)

        api_client.post(
            _save_toggle_url(),
            {"content_type": "post", "object_id": post.pk},
            format="json",
        )
        response = api_client.post(
            _save_toggle_url(),
            {"content_type": "post", "object_id": post.pk},
            format="json",
        )

        assert response.status_code == 200
        assert Save.objects.filter(user=saver).count() == 1

    def test_unsave_post_removes_row(self, api_client):
        saver = _make_user("customer", "p054-s3@example.com")
        post = _make_post()
        api_client.force_authenticate(user=saver)
        api_client.post(
            _save_toggle_url(),
            {"content_type": "post", "object_id": post.pk},
            format="json",
        )

        response = api_client.delete(
            _save_toggle_url(),
            {"content_type": "post", "object_id": post.pk},
            format="json",
        )

        assert response.status_code == 200
        assert response.json() == {"saved": False}
        assert Save.objects.filter(user=saver).count() == 0

    def test_unsave_never_saved_is_harmless_noop(self, api_client):
        saver = _make_user("customer", "p054-s4@example.com")
        post = _make_post()
        api_client.force_authenticate(user=saver)

        response = api_client.delete(
            _save_toggle_url(),
            {"content_type": "post", "object_id": post.pk},
            format="json",
        )

        assert response.status_code == 200
        assert response.json() == {"saved": False}


class TestSaveToggleReelAndProduct:
    def test_save_reel_creates_row(self, api_client):
        saver = _make_user("customer", "p054-sr1@example.com")
        reel = _make_reel()
        api_client.force_authenticate(user=saver)

        response = api_client.post(
            _save_toggle_url(),
            {"content_type": "reel", "object_id": reel.pk},
            format="json",
        )

        assert response.status_code == 200
        reel_ct = ContentType.objects.get_for_model(Reel)
        assert (
            Save.objects.filter(
                user=saver, content_type=reel_ct, object_id=reel.pk
            ).count()
            == 1
        )

    def test_save_product_creates_row(self, api_client):
        """
        The one content type Like doesn't support — proves Save's own
        whitelist (SAVE_ALLOWED_CONTENT_TYPES) genuinely includes
        Product, distinct from LikeToggleView's whitelist.
        """
        saver = _make_user("customer", "p054-sp1@example.com")
        product = _make_product()
        api_client.force_authenticate(user=saver)

        response = api_client.post(
            _save_toggle_url(),
            {"content_type": "product", "object_id": product.pk},
            format="json",
        )

        assert response.status_code == 200
        product_ct = ContentType.objects.get_for_model(Product)
        assert (
            Save.objects.filter(
                user=saver, content_type=product_ct, object_id=product.pk
            ).count()
            == 1
        )

    def test_unsave_product_removes_row(self, api_client):
        saver = _make_user("customer", "p054-sp2@example.com")
        product = _make_product()
        api_client.force_authenticate(user=saver)
        api_client.post(
            _save_toggle_url(),
            {"content_type": "product", "object_id": product.pk},
            format="json",
        )

        response = api_client.delete(
            _save_toggle_url(),
            {"content_type": "product", "object_id": product.pk},
            format="json",
        )

        assert response.status_code == 200
        assert Save.objects.filter(user=saver).count() == 0


class TestSaveToggleValidation:
    def test_unrecognized_content_type_returns_400(self, api_client):
        saver = _make_user("customer", "p054-v1@example.com")
        api_client.force_authenticate(user=saver)

        response = api_client.post(
            _save_toggle_url(), {"content_type": "story", "object_id": 1}, format="json"
        )

        assert response.status_code == 400

    def test_missing_object_id_returns_400(self, api_client):
        saver = _make_user("customer", "p054-v2@example.com")
        api_client.force_authenticate(user=saver)

        response = api_client.post(
            _save_toggle_url(), {"content_type": "product"}, format="json"
        )

        assert response.status_code == 400

    def test_nonexistent_object_id_returns_404(self, api_client):
        saver = _make_user("customer", "p054-v3@example.com")
        api_client.force_authenticate(user=saver)

        response = api_client.post(
            _save_toggle_url(),
            {"content_type": "product", "object_id": 999999},
            format="json",
        )

        assert response.status_code == 404

    def test_unauthenticated_save_returns_401(self, api_client):
        post = _make_post()
        response = api_client.post(
            _save_toggle_url(),
            {"content_type": "post", "object_id": post.pk},
            format="json",
        )
        assert response.status_code == 401


class TestSaveList:
    def test_list_returns_only_own_saves(self, api_client):
        saver = _make_user("customer", "p054-l1@example.com")
        other = _make_user("customer", "p054-l2@example.com")
        post = _make_post()
        reel = _make_reel()
        api_client.force_authenticate(user=saver)
        api_client.post(
            _save_toggle_url(),
            {"content_type": "post", "object_id": post.pk},
            format="json",
        )
        api_client.force_authenticate(user=other)
        api_client.post(
            _save_toggle_url(),
            {"content_type": "reel", "object_id": reel.pk},
            format="json",
        )

        api_client.force_authenticate(user=saver)
        response = api_client.get(_save_list_url())

        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 1
        assert results[0]["content_type"] == "post"
        assert results[0]["object_id"] == post.pk

    def test_list_includes_preview_for_each_content_type(self, api_client):
        saver = _make_user("customer", "p054-l3@example.com")
        product = _make_product()
        api_client.force_authenticate(user=saver)
        api_client.post(
            _save_toggle_url(),
            {"content_type": "product", "object_id": product.pk},
            format="json",
        )

        response = api_client.get(_save_list_url())

        assert response.status_code == 200
        item = response.json()["results"][0]
        assert item["content_type"] == "product"
        assert item["preview"]["preview_text"] == "Classic Shirt"

    def test_list_is_paginated_by_cursor(self, api_client):
        saver = _make_user("customer", "p054-l4@example.com")
        api_client.force_authenticate(user=saver)
        for _ in range(3):
            post = _make_post()
            api_client.post(
                _save_toggle_url(),
                {"content_type": "post", "object_id": post.pk},
                format="json",
            )

        response = api_client.get(_save_list_url())

        assert response.status_code == 200
        body = response.json()
        assert "results" in body and "next" in body

    def test_list_empty_when_no_saves(self, api_client):
        saver = _make_user("customer", "p054-l5@example.com")
        api_client.force_authenticate(user=saver)

        response = api_client.get(_save_list_url())

        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_unauthenticated_list_returns_401(self, api_client):
        response = api_client.get(_save_list_url())
        assert response.status_code == 401

    def test_list_has_no_id_parameter_to_manipulate_idor(self, api_client):
        """
        IDOR check (P-026 discipline, applied to a list this time):
        the URL itself carries no id — /saves/me/ always resolves from
        request.user. There is structurally no way to pass another
        user's id to see their saves.
        """
        saver = _make_user("customer", "p054-l6@example.com")
        other = _make_user("customer", "p054-l7@example.com")
        post = _make_post()
        api_client.force_authenticate(user=other)
        api_client.post(
            _save_toggle_url(),
            {"content_type": "post", "object_id": post.pk},
            format="json",
        )

        api_client.force_authenticate(user=saver)
        response = api_client.get(_save_list_url())

        assert response.status_code == 200
        assert response.json()["results"] == []


from moderation.models import Moderatable, ModerationQueue
from social.models import Comment
from social.serializers import COMMENT_MAX_LENGTH


def _comments_url():
    return reverse("comments:collection")


def _publish(obj):
    """Make a Post/Reel visible to published_objects without going
    through the moderation queue (update() fires no signals)."""
    fields = {"status": Moderatable.Status.PUBLISHED}
    if isinstance(obj, Reel):
        fields["processing_status"] = Reel.ProcessingStatus.READY
    type(obj).objects.filter(pk=obj.pk).update(**fields)
    obj.refresh_from_db()
    return obj


_MISSING = object()


class TestCommentCreate:
    def test_comment_on_post_returns_201_and_increments_counter(self, api_client):
        user = _make_user("customer", "p055-c1@example.com")
        post = _publish(_make_post())
        api_client.force_authenticate(user=user)

        response = api_client.post(
            _comments_url(),
            {"content_type": "post", "object_id": post.pk, "text": "nice"},
            format="json",
        )

        assert response.status_code == 201
        body = response.json()
        assert body["text"] == "nice"
        assert body["content_type"] == "post"
        assert body["object_id"] == post.pk
        assert body["user"] == user.pk
        assert body["is_hidden"] is False
        assert "reports_count" not in body
        assert Comment.objects.filter(user=user, object_id=post.pk).count() == 1
        post.refresh_from_db()
        assert post.comments_count == 1

    def test_comment_on_reel_returns_201_and_increments_counter(self, api_client):
        user = _make_user("customer", "p055-c2@example.com")
        reel = _publish(_make_reel())
        api_client.force_authenticate(user=user)

        response = api_client.post(
            _comments_url(),
            {"content_type": "reel", "object_id": reel.pk, "text": "wow"},
            format="json",
        )

        assert response.status_code == 201
        assert response.json()["content_type"] == "reel"
        reel.refresh_from_db()
        assert reel.comments_count == 1

    def test_creating_comment_creates_zero_moderation_queue_rows(self, api_client):
        user = _make_user("customer", "p055-c3@example.com")
        post = _publish(_make_post())
        api_client.force_authenticate(user=user)
        before = ModerationQueue.objects.count()
        # Sanity: the Post itself WAS enqueued, so the moderation
        # signal is live and this negative test is meaningful.
        assert before >= 1

        response = api_client.post(
            _comments_url(),
            {"content_type": "post", "object_id": post.pk, "text": "nice"},
            format="json",
        )

        assert response.status_code == 201
        assert ModerationQueue.objects.count() == before
        comment = Comment.objects.get(pk=response.json()["id"])
        assert not hasattr(comment, "status")

    def test_each_comment_increments_counter_by_one(self, api_client):
        user = _make_user("customer", "p055-c4@example.com")
        post = _publish(_make_post())
        api_client.force_authenticate(user=user)
        payload = {"content_type": "post", "object_id": post.pk, "text": "hi"}

        api_client.post(_comments_url(), payload, format="json")
        api_client.post(_comments_url(), payload, format="json")

        post.refresh_from_db()
        assert post.comments_count == 2
        assert Comment.objects.filter(object_id=post.pk).count() == 2

    def test_business_account_can_comment_too(self, api_client):
        post = _publish(_make_post())
        other_business_owner = _make_user("business", "p055-c5@example.com")
        api_client.force_authenticate(user=other_business_owner)

        response = api_client.post(
            _comments_url(),
            {"content_type": "post", "object_id": post.pk, "text": "hello"},
            format="json",
        )

        assert response.status_code == 201

    @pytest.mark.parametrize(
        "field,value",
        [
            ("content_type", "story"),
            ("content_type", "product"),
            ("content_type", _MISSING),
            ("object_id", _MISSING),
            ("object_id", "abc"),
            ("object_id", 0),
            ("text", _MISSING),
            ("text", ""),
            ("text", "   "),
            ("text", "x" * (COMMENT_MAX_LENGTH + 1)),
        ],
    )
    def test_invalid_payload_returns_400_and_creates_nothing(
        self, api_client, field, value
    ):
        user = _make_user("customer", "p055-v1@example.com")
        post = _publish(_make_post())
        api_client.force_authenticate(user=user)
        payload = {"content_type": "post", "object_id": post.pk, "text": "nice"}
        if value is _MISSING:
            del payload[field]
        else:
            payload[field] = value

        response = api_client.post(_comments_url(), payload, format="json")

        assert response.status_code == 400
        assert Comment.objects.count() == 0
        post.refresh_from_db()
        assert post.comments_count == 0

    def test_nonexistent_target_returns_404(self, api_client):
        user = _make_user("customer", "p055-v2@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.post(
            _comments_url(),
            {"content_type": "post", "object_id": 999999, "text": "nice"},
            format="json",
        )

        assert response.status_code == 404

    def test_unpublished_post_returns_404_and_creates_nothing(self, api_client):
        user = _make_user("customer", "p055-v3@example.com")
        post = _make_post()  # pending_review, never published
        api_client.force_authenticate(user=user)

        response = api_client.post(
            _comments_url(),
            {"content_type": "post", "object_id": post.pk, "text": "nice"},
            format="json",
        )

        assert response.status_code == 404
        assert Comment.objects.count() == 0
        post.refresh_from_db()
        assert post.comments_count == 0

    def test_soft_deleted_post_returns_404(self, api_client):
        user = _make_user("customer", "p055-v4@example.com")
        post = _publish(_make_post())
        post.delete()  # soft delete
        api_client.force_authenticate(user=user)

        response = api_client.post(
            _comments_url(),
            {"content_type": "post", "object_id": post.pk, "text": "nice"},
            format="json",
        )

        assert response.status_code == 404

    def test_unauthenticated_returns_401(self, api_client):
        post = _publish(_make_post())

        response = api_client.post(
            _comments_url(),
            {"content_type": "post", "object_id": post.pk, "text": "nice"},
            format="json",
        )

        assert response.status_code == 401
        assert Comment.objects.count() == 0


from django.contrib.auth.models import Group

from social.services import (
    COMMENT_AUTO_HIDE_THRESHOLD,
    check_and_hide_if_threshold_exceeded,
)


def _make_moderator(email):
    user = _make_user("customer", email)
    user.groups.add(Group.objects.get(name="Moderator"))
    return user


def _add_comment(user, target, text="nice", **extra):
    return Comment.objects.create(
        user=user,
        content_type=ContentType.objects.get_for_model(type(target)),
        object_id=target.pk,
        text=text,
        **extra,
    )


def _list_comments(client, content_type, object_id):
    return client.get(
        _comments_url(), {"content_type": content_type, "object_id": object_id}
    )


def _comment_ids(response):
    return [item["id"] for item in response.json()["results"]]


class TestCommentList:
    def test_comment_is_listable_immediately_after_creation(self, api_client):
        author = _make_user("customer", "p055-l1@example.com")
        post = _publish(_make_post())
        api_client.force_authenticate(user=author)
        created = api_client.post(
            _comments_url(),
            {"content_type": "post", "object_id": post.pk, "text": "nice"},
            format="json",
        )
        assert created.status_code == 201

        anonymous = APIClient()
        response = _list_comments(anonymous, "post", post.pk)

        assert response.status_code == 200
        assert _comment_ids(response) == [created.json()["id"]]

    def test_list_is_scoped_to_the_requested_target(self, api_client):
        user = _make_user("customer", "p055-l2@example.com")
        post = _publish(_make_post())
        other_post = _publish(_make_post())
        mine = _add_comment(user, post, "on this post")
        _add_comment(user, other_post, "on another post")
        # Same object_id but a DIFFERENT content type must not leak in.
        Comment.objects.create(
            user=user,
            content_type=ContentType.objects.get_for_model(Reel),
            object_id=post.pk,
            text="on a reel that happens to share the id",
        )

        response = _list_comments(api_client, "post", post.pk)

        assert response.status_code == 200
        assert _comment_ids(response) == [mine.pk]

    def test_list_is_newest_first_and_cursor_paginated(self, api_client):
        user = _make_user("customer", "p055-l3@example.com")
        post = _publish(_make_post())
        first = _add_comment(user, post, "first")
        second = _add_comment(user, post, "second")
        third = _add_comment(user, post, "third")

        response = _list_comments(api_client, "post", post.pk)

        body = response.json()
        assert "results" in body and "next" in body and "previous" in body
        assert _comment_ids(response) == [third.pk, second.pk, first.pk]

    def test_hidden_comment_excluded_for_anonymous(self, api_client):
        author = _make_user("customer", "p055-h1@example.com")
        post = _publish(_make_post())
        visible = _add_comment(author, post, "visible")
        _add_comment(author, post, "hidden", is_hidden=True)

        response = _list_comments(api_client, "post", post.pk)

        assert _comment_ids(response) == [visible.pk]

    def test_hidden_comment_excluded_for_unrelated_authenticated_user(self, api_client):
        author = _make_user("customer", "p055-h2@example.com")
        stranger = _make_user("customer", "p055-h2b@example.com")
        post = _publish(_make_post())
        visible = _add_comment(author, post, "visible")
        _add_comment(author, post, "hidden", is_hidden=True)
        api_client.force_authenticate(user=stranger)

        response = _list_comments(api_client, "post", post.pk)

        assert _comment_ids(response) == [visible.pk]

    def test_hidden_comment_included_for_its_own_author(self, api_client):
        author = _make_user("customer", "p055-h3@example.com")
        post = _publish(_make_post())
        hidden = _add_comment(author, post, "hidden", is_hidden=True)
        api_client.force_authenticate(user=author)

        response = _list_comments(api_client, "post", post.pk)

        assert _comment_ids(response) == [hidden.pk]
        assert response.json()["results"][0]["is_hidden"] is True

    def test_author_sees_own_hidden_comment_but_not_others_hidden(self, api_client):
        author_a = _make_user("customer", "p055-h4a@example.com")
        author_b = _make_user("customer", "p055-h4b@example.com")
        post = _publish(_make_post())
        a_hidden = _add_comment(author_a, post, "a hidden", is_hidden=True)
        _add_comment(author_b, post, "b hidden", is_hidden=True)
        b_visible = _add_comment(author_b, post, "b visible")
        api_client.force_authenticate(user=author_a)

        response = _list_comments(api_client, "post", post.pk)

        assert set(_comment_ids(response)) == {a_hidden.pk, b_visible.pk}

    def test_hidden_comment_included_for_moderator(self, api_client):
        author = _make_user("customer", "p055-h5@example.com")
        moderator = _make_moderator("p055-h5m@example.com")
        post = _publish(_make_post())
        visible = _add_comment(author, post, "visible")
        hidden = _add_comment(author, post, "hidden", is_hidden=True)
        api_client.force_authenticate(user=moderator)

        response = _list_comments(api_client, "post", post.pk)

        assert set(_comment_ids(response)) == {visible.pk, hidden.pk}

    def test_soft_deleted_comment_excluded_for_everyone(self, api_client):
        author = _make_user("customer", "p055-d1@example.com")
        moderator = _make_moderator("p055-d1m@example.com")
        post = _publish(_make_post())
        gone = _add_comment(author, post, "deleted")
        gone.delete()  # soft delete

        for viewer in (None, author, moderator):
            client = APIClient()
            if viewer is not None:
                client.force_authenticate(user=viewer)
            response = _list_comments(client, "post", post.pk)
            assert response.status_code == 200
            assert _comment_ids(response) == []

    def test_auto_hide_end_to_end_via_service_seam(self, api_client):
        author = _make_user("customer", "p055-e1@example.com")
        post = _publish(_make_post())
        comment = _add_comment(author, post, "controversial")
        assert _comment_ids(_list_comments(APIClient(), "post", post.pk)) == [
            comment.pk
        ]

        Comment.objects.filter(pk=comment.pk).update(
            reports_count=COMMENT_AUTO_HIDE_THRESHOLD
        )
        assert check_and_hide_if_threshold_exceeded(comment) is True

        assert _comment_ids(_list_comments(APIClient(), "post", post.pk)) == []
        api_client.force_authenticate(user=author)
        assert _comment_ids(_list_comments(api_client, "post", post.pk)) == [comment.pk]

    @pytest.mark.parametrize(
        "params",
        [
            {"object_id": 1},
            {"content_type": "post"},
            {"content_type": "post", "object_id": "abc"},
            {"content_type": "post", "object_id": 0},
            {"content_type": "story", "object_id": 1},
        ],
    )
    def test_invalid_query_returns_400(self, api_client, params):
        response = api_client.get(_comments_url(), params)

        assert response.status_code == 400

    def test_nonexistent_target_returns_404(self, api_client):
        response = _list_comments(api_client, "post", 999999)

        assert response.status_code == 404

    def test_unpublished_target_returns_404(self, api_client):
        post = _make_post()  # pending_review

        response = _list_comments(api_client, "post", post.pk)

        assert response.status_code == 404

    def test_delete_on_collection_returns_405(self, api_client):
        user = _make_user("customer", "p055-m1@example.com")
        api_client.force_authenticate(user=user)

        response = api_client.delete(_comments_url())

        assert response.status_code == 405


@pytest.mark.django_db(transaction=True)
class TestCommentCountConcurrency:
    def test_concurrent_comments_each_increment_counter_exactly_once(self):
        commenter = _make_user("customer", "p055-conc1@example.com")
        post = _publish(_make_post())
        results = []

        def _do_comment():
            client = APIClient()
            client.force_authenticate(user=commenter)
            resp = client.post(
                _comments_url(),
                {"content_type": "post", "object_id": post.pk, "text": "race"},
                format="json",
            )
            results.append(resp.status_code)

        t1 = threading.Thread(target=_do_comment)
        t2 = threading.Thread(target=_do_comment)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results == [201, 201]
        assert Comment.objects.filter(object_id=post.pk).count() == 2
        post.refresh_from_db()
        assert post.comments_count == 2


from social.models import Share


def _shares_url():
    return reverse("shares:create")


@pytest.mark.django_db
class TestShareCreate:
    def test_share_post_returns_201_and_increments_counter(self, api_client):
        user = _make_user("customer", "p056-s1@example.com")
        post = _publish(_make_post())
        api_client.force_authenticate(user=user)

        response = api_client.post(
            _shares_url(),
            {"content_type": "post", "object_id": post.pk},
            format="json",
        )

        assert response.status_code == 201
        assert response.json() == {"shared": True}
        post_ct = ContentType.objects.get_for_model(Post)
        assert (
            Share.objects.filter(
                user=user, content_type=post_ct, object_id=post.pk
            ).count()
            == 1
        )
        post.refresh_from_db()
        assert post.shares_count == 1

    def test_share_reel_returns_201_and_increments_counter(self, api_client):
        user = _make_user("customer", "p056-s2@example.com")
        reel = _publish(_make_reel())
        api_client.force_authenticate(user=user)

        response = api_client.post(
            _shares_url(),
            {"content_type": "reel", "object_id": reel.pk},
            format="json",
        )

        assert response.status_code == 201
        reel_ct = ContentType.objects.get_for_model(Reel)
        assert (
            Share.objects.filter(
                user=user, content_type=reel_ct, object_id=reel.pk
            ).count()
            == 1
        )
        reel.refresh_from_db()
        assert reel.shares_count == 1

    def test_sharing_same_content_twice_is_not_deduplicated(self, api_client):
        """
        DELIBERATELY the OPPOSITE assertion of every prior Phase 9
        idempotency test (P-052 Follow, P-053 Like, P-054 Save).

        Those tests prove that repeating the action creates NO second
        row and NO second increment. Share is a genuine repeatable
        event, so here repeating the action MUST create a second row
        and MUST increment the counter a second time. If this test
        ever fails because someone "fixed" Share into an idempotent
        toggle, the fix is the bug — revert it.
        """
        user = _make_user("customer", "p056-s3@example.com")
        post = _publish(_make_post())
        api_client.force_authenticate(user=user)
        payload = {"content_type": "post", "object_id": post.pk}

        first = api_client.post(_shares_url(), payload, format="json")
        second = api_client.post(_shares_url(), payload, format="json")

        assert first.status_code == 201
        assert second.status_code == 201
        assert Share.objects.filter(user=user, object_id=post.pk).count() == 2
        post.refresh_from_db()
        assert post.shares_count == 2

    def test_different_users_sharing_same_content_are_each_counted(self, api_client):
        post = _publish(_make_post())
        payload = {"content_type": "post", "object_id": post.pk}
        for email in ("p056-s4a@example.com", "p056-s4b@example.com"):
            api_client.force_authenticate(user=_make_user("customer", email))
            assert (
                api_client.post(_shares_url(), payload, format="json").status_code
                == 201
            )

        assert Share.objects.filter(object_id=post.pk).count() == 2
        post.refresh_from_db()
        assert post.shares_count == 2

    def test_share_does_not_touch_other_counters(self, api_client):
        user = _make_user("customer", "p056-s5@example.com")
        post = _publish(_make_post())
        api_client.force_authenticate(user=user)

        api_client.post(
            _shares_url(),
            {"content_type": "post", "object_id": post.pk},
            format="json",
        )

        post.refresh_from_db()
        assert post.shares_count == 1
        assert post.likes_count == 0
        assert post.comments_count == 0

    def test_share_does_not_affect_other_objects(self, api_client):
        user = _make_user("customer", "p056-s6@example.com")
        target = _publish(_make_post())
        other = _publish(_make_post())
        api_client.force_authenticate(user=user)

        api_client.post(
            _shares_url(),
            {"content_type": "post", "object_id": target.pk},
            format="json",
        )

        other.refresh_from_db()
        assert other.shares_count == 0

    def test_share_creates_no_moderation_queue_row(self, api_client):
        user = _make_user("customer", "p056-s7@example.com")
        post = _publish(_make_post())
        api_client.force_authenticate(user=user)
        before = ModerationQueue.objects.count()
        # Sanity: the Post itself WAS enqueued, so the moderation
        # signal is live and this negative test is meaningful.
        assert before >= 1

        response = api_client.post(
            _shares_url(),
            {"content_type": "post", "object_id": post.pk},
            format="json",
        )

        assert response.status_code == 201
        assert ModerationQueue.objects.count() == before


@pytest.mark.django_db
class TestShareValidation:
    def _authed(self, api_client, email):
        api_client.force_authenticate(user=_make_user("customer", email))
        return api_client

    def test_story_content_type_returns_400(self, api_client):
        client = self._authed(api_client, "p056-v1@example.com")
        response = client.post(
            _shares_url(), {"content_type": "story", "object_id": 1}, format="json"
        )
        assert response.status_code == 400
        assert Share.objects.count() == 0

    def test_product_content_type_returns_400(self, api_client):
        client = self._authed(api_client, "p056-v2@example.com")
        response = client.post(
            _shares_url(), {"content_type": "product", "object_id": 1}, format="json"
        )
        assert response.status_code == 400
        assert Share.objects.count() == 0

    def test_missing_content_type_returns_400(self, api_client):
        client = self._authed(api_client, "p056-v3@example.com")
        response = client.post(_shares_url(), {"object_id": 1}, format="json")
        assert response.status_code == 400

    def test_missing_object_id_returns_400(self, api_client):
        client = self._authed(api_client, "p056-v4@example.com")
        response = client.post(_shares_url(), {"content_type": "post"}, format="json")
        assert response.status_code == 400

    def test_zero_object_id_returns_400(self, api_client):
        client = self._authed(api_client, "p056-v5@example.com")
        response = client.post(
            _shares_url(), {"content_type": "post", "object_id": 0}, format="json"
        )
        assert response.status_code == 400

    def test_non_integer_object_id_returns_400(self, api_client):
        client = self._authed(api_client, "p056-v6@example.com")
        response = client.post(
            _shares_url(), {"content_type": "post", "object_id": "abc"}, format="json"
        )
        assert response.status_code == 400

    def test_nonexistent_object_returns_404(self, api_client):
        client = self._authed(api_client, "p056-v7@example.com")
        response = client.post(
            _shares_url(),
            {"content_type": "post", "object_id": 999999},
            format="json",
        )
        assert response.status_code == 404
        assert Share.objects.count() == 0

    def test_unpublished_post_returns_404_with_no_side_effects(self, api_client):
        client = self._authed(api_client, "p056-v8@example.com")
        post = _make_post()  # default status: pending_review (unpublished)

        response = client.post(
            _shares_url(),
            {"content_type": "post", "object_id": post.pk},
            format="json",
        )

        assert response.status_code == 404
        assert Share.objects.count() == 0
        post.refresh_from_db()
        assert post.shares_count == 0

    def test_unpublished_reel_returns_404_with_no_side_effects(self, api_client):
        client = self._authed(api_client, "p056-v9@example.com")
        reel = _make_reel()  # default: pending_review + not yet processed

        response = client.post(
            _shares_url(),
            {"content_type": "reel", "object_id": reel.pk},
            format="json",
        )

        assert response.status_code == 404
        assert Share.objects.count() == 0
        reel.refresh_from_db()
        assert reel.shares_count == 0

    def test_unauthenticated_share_returns_401_with_no_side_effects(self, api_client):
        post = _publish(_make_post())
        response = api_client.post(
            _shares_url(),
            {"content_type": "post", "object_id": post.pk},
            format="json",
        )
        assert response.status_code == 401
        assert Share.objects.count() == 0
        post.refresh_from_db()
        assert post.shares_count == 0

    def test_get_is_not_allowed(self, api_client):
        client = self._authed(api_client, "p056-v10@example.com")
        assert client.get(_shares_url()).status_code == 405


from social.views import ShareCreateView


@pytest.mark.django_db(transaction=True)
class TestShareCountConcurrency:
    def test_concurrent_shares_are_both_counted(self):
        """
        Share has no idempotency concern, but the atomicity of the
        counter increment still matters: two near-simultaneous shares
        must BOTH register (2 rows, shares_count == 2) rather than one
        being lost to a read-then-write race.
        """
        sharer = _make_user("customer", "p056-conc1@example.com")
        post = _publish(_make_post())
        results = []

        def _do_share():
            client = APIClient()
            client.force_authenticate(user=sharer)
            resp = client.post(
                _shares_url(),
                {"content_type": "post", "object_id": post.pk},
                format="json",
            )
            results.append(resp.status_code)

        t1 = threading.Thread(target=_do_share)
        t2 = threading.Thread(target=_do_share)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results == [201, 201]
        assert Share.objects.filter(object_id=post.pk).count() == 2
        post.refresh_from_db()
        assert post.shares_count == 2


class TestShareViewStructure:
    def test_view_does_not_use_get_or_create(self):
        # Guard against someone copying the toggle pattern from
        # Like/Follow (P-052/P-053) into Share.
        source = inspect.getsource(ShareCreateView.post)
        assert "get_or_create" not in source

    def test_view_uses_atomic_f_increment(self):
        source = inspect.getsource(ShareCreateView.post)
        assert "transaction.atomic" in source
        assert 'F("shares_count")' in source
