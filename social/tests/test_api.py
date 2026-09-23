"""
API tests for Part P-052 — FollowToggleView.

Mirrors businesses/tests/test_api.py's conventions (force_authenticate,
APIClient, pytest.mark.django_db). The concurrency test is the one
genuine exception: it needs @pytest.mark.django_db(transaction=True)
so two threads get real, separately-committed Postgres transactions
instead of sharing one wrapping test transaction — otherwise a race
condition could never actually manifest in the test.
"""

import threading

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
    owner = _make_user("business", f"p052-owner-{id(object())}@example.com")
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
            Like.objects.filter(user=liker, content_type=post_ct, object_id=post.pk).count()
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

        response = api_client.post(
            _like_url(), {"content_type": "post"}, format="json"
        )

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
        assert Like.objects.filter(user=liker, content_type=post_ct, object_id=post.pk).count() == 1
        post.refresh_from_db()
        assert post.likes_count == 1