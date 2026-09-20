from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from businesses.services import create_business_profile
from content.models import Post
from core.tests.test_media import _DISGUISED_EXE_BYTES, _VALID_PNG_BYTES
from moderation.models import ModerationQueue

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


class TestPostCreate(APITestCase):
    def setUp(self):
        self.user, self.business = _make_business_user(
            "trader-a@example.com", "Trader A"
        )

    def test_create_post_auto_enqueues_moderation(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/posts/", {"caption": "hello customers"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        count = ModerationQueue.objects.filter(
            content_type=ContentType.objects.get_for_model(Post),
            object_id=response.data["id"],
        ).count()
        self.assertEqual(count, 1)

    def test_business_field_in_body_is_ignored_not_honored(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/posts/",
            {"caption": "hi", "business": 999999},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        post = Post.objects.get(pk=response.data["id"])
        self.assertEqual(post.business_id, self.business.id)

    def test_status_in_body_is_ignored_on_create(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/posts/",
            {"caption": "hi", "status": "published"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        post = Post.objects.get(pk=response.data["id"])
        self.assertEqual(post.status, "pending_review")

    def test_unauthenticated_create_rejected(self):
        response = self.client.post(
            "/api/v1/posts/", {"caption": "hi"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_without_business_profile_gets_403_on_create(self):
        customer = _make_customer("customer-a@example.com")
        self.client.force_authenticate(customer)
        response = self.client.post(
            "/api/v1/posts/", {"caption": "hi"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TestPostOwnList(APITestCase):
    def setUp(self):
        self.user_a, self.business_a = _make_business_user(
            "trader-a2@example.com", "Trader A2"
        )
        self.user_b, self.business_b = _make_business_user(
            "trader-b@example.com", "Trader B"
        )
        self.post_a = Post.objects.create(business=self.business_a, caption="A's post")
        self.post_b = Post.objects.create(business=self.business_b, caption="B's post")

    def test_own_list_excludes_other_business_posts(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.get("/api/v1/posts/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.post_a.id, ids)
        self.assertNotIn(self.post_b.id, ids)

    def test_customer_without_business_profile_sees_empty_list(self):
        customer = _make_customer("customer-b@example.com")
        self.client.force_authenticate(customer)
        response = self.client.get("/api/v1/posts/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], [])

    def test_unauthenticated_list_rejected(self):
        response = self.client.get("/api/v1/posts/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class TestPostDetailIDOR(APITestCase):
    def setUp(self):
        self.user_a, self.business_a = _make_business_user(
            "trader-a3@example.com", "Trader A3"
        )
        self.user_b, self.business_b = _make_business_user(
            "trader-b2@example.com", "Trader B2"
        )
        self.post = Post.objects.create(
            business=self.business_a, caption="original caption"
        )

    def test_public_get_requires_no_auth(self):
        response = self.client.get(f"/api/v1/posts/{self.post.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unknown_id_returns_404_not_found_code(self):
        response = self.client.get("/api/v1/posts/999999/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"]["code"], "NOT_FOUND")

    def test_other_business_cannot_patch_post(self):
        self.client.force_authenticate(self.user_b)
        response = self.client.patch(
            f"/api/v1/posts/{self.post.id}/",
            {"caption": "hijacked"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.post.refresh_from_db()
        self.assertEqual(self.post.caption, "original caption")

    def test_other_business_cannot_delete_post(self):
        self.client.force_authenticate(self.user_b)
        response = self.client.delete(f"/api/v1/posts/{self.post.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_deleted)

    def test_owner_can_patch_caption(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.patch(
            f"/api/v1/posts/{self.post.id}/",
            {"caption": "updated caption"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.post.refresh_from_db()
        self.assertEqual(self.post.caption, "updated caption")

    def test_status_not_writable_via_patch(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.patch(
            f"/api/v1/posts/{self.post.id}/",
            {"status": "published"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, "pending_review")

    def test_owner_delete_is_soft_delete(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.delete(f"/api/v1/posts/{self.post.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.post.refresh_from_db()
        self.assertTrue(self.post.is_deleted)

    def test_unauthenticated_patch_rejected(self):
        response = self.client.patch(
            f"/api/v1/posts/{self.post.id}/", {"caption": "x"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unauthenticated_delete_rejected(self):
        response = self.client.delete(f"/api/v1/posts/{self.post.id}/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class TestPostImageUpload(APITestCase):
    def setUp(self):
        self.user, self.business = _make_business_user(
            "trader-img@example.com", "Trader Img"
        )

    def test_valid_image_accepted(self):
        self.client.force_authenticate(self.user)
        image = SimpleUploadedFile(
            "post.png", _VALID_PNG_BYTES, content_type="image/png"
        )
        response = self.client.post(
            "/api/v1/posts/",
            {"caption": "with image", "image": image},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_spoofed_extension_image_is_rejected(self):
        self.client.force_authenticate(self.user)
        image = SimpleUploadedFile(
            "post.png", _DISGUISED_EXE_BYTES, content_type="image/png"
        )
        response = self.client.post(
            "/api/v1/posts/",
            {"caption": "bad image", "image": image},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_post_omitting_image_is_still_valid(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/posts/", {"caption": "no image"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class TestPostModerationPreview(APITestCase):
    """
    Proves get_moderation_preview() reaches the real moderator queue API
    (P-038) with real caption data, not the generic str(self)[:200]
    fallback — the explicit acceptance criterion for this part.
    """

    def setUp(self):
        self.user, self.business = _make_business_user(
            "trader-prev@example.com", "Trader Preview"
        )
        self.moderator = _make_customer("moderator-p041@example.com")
        self.moderator.groups.add(Group.objects.get(name="Moderator"))

    def test_preview_shows_real_caption_not_generic_fallback(self):
        self.client.force_authenticate(self.user)
        create_response = self.client.post(
            "/api/v1/posts/",
            {"caption": "A very specific caption for the moderator to see"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        post_id = create_response.data["id"]

        self.client.force_authenticate(self.moderator)
        queue_response = self.client.get("/api/v1/moderation/queue/")
        self.assertEqual(queue_response.status_code, status.HTTP_200_OK)

        item = next(
            entry
            for entry in queue_response.data["results"]
            if entry["content_type"] == "post" and entry["object_id"] == post_id
        )
        self.assertEqual(
            item["preview"]["preview_text"],
            "A very specific caption for the moderator to see",
        )
        self.assertEqual(item["submitter"]["business_name"], "Trader Preview")