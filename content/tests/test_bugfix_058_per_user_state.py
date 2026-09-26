"""
Part BUGFIX-058 — per-user is_liked/is_saved/likes_count/comments_count/
shares_count on the public Post/Reel serializers.

Covers the exact acceptance criteria from the part spec: two different
users, same object, see different is_liked/is_saved from the same
public endpoint; an anonymous request sees False rather than an error;
and the existing denormalized counters are now actually serialized.
"""

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from businesses.services import create_business_profile
from content.models import Post, Reel
from social.models import Like, Save

User = get_user_model()


def _make_business_user(email, business_name):
    user = User.objects.create_user(
        username=email, email=email, password="testpass123", account_type="business"
    )
    business = create_business_profile(
        user=user,
        business_name=business_name,
        business_type="trader",
        country="EG",
        city="Ismailia",
    )
    return user, business


def _make_customer(email):
    return User.objects.create_user(
        username=email, email=email, password="testpass123", account_type="customer"
    )


class TestPostPublicSerializerPerUserState(APITestCase):
    def setUp(self):
        self.owner, self.business = _make_business_user(
            "trader-bugfix058-post@example.com", "Trader BUGFIX-058 Post"
        )
        self.post = Post.objects.create(
            business=self.business,
            caption="post under test",
            status="published",
            likes_count=3,
            comments_count=1,
            shares_count=2,
        )
        self.user_a = _make_customer("user-a-bugfix058@example.com")
        self.user_b = _make_customer("user-b-bugfix058@example.com")

        from django.contrib.contenttypes.models import ContentType

        Like.objects.create(
            user=self.user_a,
            content_type=ContentType.objects.get_for_model(Post),
            object_id=self.post.id,
        )

    def _get_item(self, client):
        response = client.get("/api/v1/posts/public/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        items = [item for item in response.data["results"] if item["id"] == self.post.id]
        self.assertEqual(len(items), 1)
        return items[0]

    def test_anonymous_request_sees_is_liked_false(self):
        item = self._get_item(self.client)
        self.assertFalse(item["is_liked"])
        self.assertFalse(item["is_saved"])

    def test_user_who_liked_sees_is_liked_true(self):
        self.client.force_authenticate(self.user_a)
        item = self._get_item(self.client)
        self.assertTrue(item["is_liked"])

    def test_different_user_who_did_not_like_sees_is_liked_false(self):
        self.client.force_authenticate(self.user_b)
        item = self._get_item(self.client)
        self.assertFalse(item["is_liked"])

    def test_counters_are_serialized(self):
        item = self._get_item(self.client)
        self.assertEqual(item["likes_count"], 3)
        self.assertEqual(item["comments_count"], 1)
        self.assertEqual(item["shares_count"], 2)

    def test_saved_state_is_per_user(self):
        from django.contrib.contenttypes.models import ContentType

        Save.objects.create(
            user=self.user_a,
            content_type=ContentType.objects.get_for_model(Post),
            object_id=self.post.id,
        )
        self.client.force_authenticate(self.user_a)
        self.assertTrue(self._get_item(self.client)["is_saved"])

        self.client.force_authenticate(self.user_b)
        self.assertFalse(self._get_item(self.client)["is_saved"])


class TestReelPublicSerializerPerUserState(APITestCase):
    def setUp(self):
        self.owner, self.business = _make_business_user(
            "trader-bugfix058-reel@example.com", "Trader BUGFIX-058 Reel"
        )
        self.reel = Reel.objects.create(
            business=self.business,
            caption="reel under test",
            status="published",
            processing_status="ready",
            likes_count=5,
            comments_count=0,
            shares_count=0,
        )
        self.user_a = _make_customer("reel-user-a-bugfix058@example.com")
        self.user_b = _make_customer("reel-user-b-bugfix058@example.com")

    def _get_item(self, client):
        response = client.get("/api/v1/reels/public/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        items = [item for item in response.data["results"] if item["id"] == self.reel.id]
        self.assertEqual(len(items), 1)
        return items[0]

    def test_two_users_same_reel_different_is_liked(self):
        from django.contrib.contenttypes.models import ContentType

        Like.objects.create(
            user=self.user_a,
            content_type=ContentType.objects.get_for_model(Reel),
            object_id=self.reel.id,
        )

        self.client.force_authenticate(self.user_a)
        self.assertTrue(self._get_item(self.client)["is_liked"])

        self.client.force_authenticate(self.user_b)
        self.assertFalse(self._get_item(self.client)["is_liked"])

    def test_reel_counters_are_serialized(self):
        item = self._get_item(self.client)
        self.assertEqual(item["likes_count"], 5)