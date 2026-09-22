import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from businesses.services import create_business_profile
from content.models import Post, Reel
from core.tests.test_media import _DISGUISED_EXE_BYTES, _VALID_PNG_BYTES
from moderation.models import ModerationQueue

User = get_user_model()

_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
with open(os.path.join(_FIXTURES_DIR, "small_test_reel.mp4"), "rb") as _f:
    _VALID_MP4_BYTES = _f.read()


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


# ---------------------------------------------------------------------------
# Part P-042: Reel API tests below. Same IDOR/ownership pattern proven
# above for Post, applied byte-for-byte to Reel, plus two things unique
# to Reel: transcode_reel dispatch on create, and (unlike Post) NO
# moderation queue row immediately after create — see
# TestReelCreateDoesNotAutoEnqueue below, the direct API-level proof of
# this whole part's point.
# ---------------------------------------------------------------------------


def _make_video_upload(name="raw.mp4"):
    return SimpleUploadedFile(name, _VALID_MP4_BYTES, content_type="video/mp4")


class TestReelCreate(APITestCase):
    def setUp(self):
        self.user, self.business = _make_business_user(
            "trader-reel-a@example.com", "Trader Reel A"
        )

    @patch("content.views.transcode_reel.delay")
    def test_create_reel_dispatches_transcode_task(self, mock_delay):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/reels/",
            {"caption": "hi", "video": _make_video_upload()},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_delay.assert_called_once_with(response.data["id"])

    @patch("content.views.transcode_reel.delay")
    def test_create_reel_starts_at_processing_status_uploaded(self, mock_delay):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/reels/",
            {"caption": "hi", "video": _make_video_upload()},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["processing_status"], "uploaded")

    @patch("content.views.transcode_reel.delay")
    def test_create_reel_does_not_auto_enqueue_moderation(self, mock_delay):
        # THE key API-level proof for this whole part: unlike Post,
        # creating a Reel must NOT create a ModerationQueue row
        # immediately — that only happens once transcode_reel (mocked
        # away here on purpose — its own real-ffmpeg correctness is
        # proven separately in content/tests/test_tasks.py) actually
        # runs and reaches "ready".
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/reels/",
            {"caption": "hi", "video": _make_video_upload()},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        count = ModerationQueue.objects.filter(
            content_type=ContentType.objects.get_for_model(Reel),
            object_id=response.data["id"],
        ).count()
        self.assertEqual(count, 0)

    @patch("content.views.transcode_reel.delay")
    def test_business_field_in_body_is_ignored_not_honored(self, mock_delay):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/reels/",
            {"caption": "hi", "business": 999999, "video": _make_video_upload()},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        reel = Reel.objects.get(pk=response.data["id"])
        self.assertEqual(reel.business_id, self.business.id)

    @patch("content.views.transcode_reel.delay")
    def test_processing_status_in_body_is_ignored_on_create(self, mock_delay):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/reels/",
            {
                "caption": "hi",
                "processing_status": "ready",
                "video": _make_video_upload(),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        reel = Reel.objects.get(pk=response.data["id"])
        self.assertEqual(reel.processing_status, "uploaded")

    def test_unauthenticated_create_rejected(self):
        response = self.client.post(
            "/api/v1/reels/",
            {"caption": "hi", "video": _make_video_upload()},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_without_business_profile_gets_403_on_create(self):
        customer = _make_customer("customer-reel-a@example.com")
        self.client.force_authenticate(customer)
        response = self.client.post(
            "/api/v1/reels/",
            {"caption": "hi", "video": _make_video_upload()},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_creating_reel_without_a_video_is_rejected(self):
        # Unlike Post.image (null=True/blank=True), Reel.video is
        # mandatory — there is nothing to transcode without it.
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/reels/", {"caption": "no video"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_spoofed_extension_video_is_rejected(self):
        self.client.force_authenticate(self.user)
        fake_video = SimpleUploadedFile(
            "raw.mp4", _DISGUISED_EXE_BYTES, content_type="video/mp4"
        )
        response = self.client.post(
            "/api/v1/reels/",
            {"caption": "bad video", "video": fake_video},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class TestReelOwnList(APITestCase):
    def setUp(self):
        self.user_a, self.business_a = _make_business_user(
            "trader-reel-b@example.com", "Trader Reel B"
        )
        self.user_b, self.business_b = _make_business_user(
            "trader-reel-c@example.com", "Trader Reel C"
        )
        self.reel_a = Reel.objects.create(
            business=self.business_a, caption="A's reel", video=_make_video_upload()
        )
        self.reel_b = Reel.objects.create(
            business=self.business_b, caption="B's reel", video=_make_video_upload()
        )

    def test_own_list_excludes_other_business_reels(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.get("/api/v1/reels/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.reel_a.id, ids)
        self.assertNotIn(self.reel_b.id, ids)

    def test_customer_without_business_profile_sees_empty_list(self):
        customer = _make_customer("customer-reel-b@example.com")
        self.client.force_authenticate(customer)
        response = self.client.get("/api/v1/reels/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], [])

    def test_unauthenticated_list_rejected(self):
        response = self.client.get("/api/v1/reels/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class TestReelDetailIDOR(APITestCase):
    def setUp(self):
        self.user_a, self.business_a = _make_business_user(
            "trader-reel-d@example.com", "Trader Reel D"
        )
        self.user_b, self.business_b = _make_business_user(
            "trader-reel-e@example.com", "Trader Reel E"
        )
        self.reel = Reel.objects.create(
            business=self.business_a,
            caption="original caption",
            video=_make_video_upload(),
        )

    def test_public_get_requires_no_auth(self):
        response = self.client.get(f"/api/v1/reels/{self.reel.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unknown_id_returns_404_not_found_code(self):
        response = self.client.get("/api/v1/reels/999999/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"]["code"], "NOT_FOUND")

    def test_other_business_cannot_patch_reel(self):
        self.client.force_authenticate(self.user_b)
        response = self.client.patch(
            f"/api/v1/reels/{self.reel.id}/",
            {"caption": "hijacked"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.reel.refresh_from_db()
        self.assertEqual(self.reel.caption, "original caption")

    def test_other_business_cannot_delete_reel(self):
        self.client.force_authenticate(self.user_b)
        response = self.client.delete(f"/api/v1/reels/{self.reel.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.reel.refresh_from_db()
        self.assertFalse(self.reel.is_deleted)

    def test_owner_can_patch_caption(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.patch(
            f"/api/v1/reels/{self.reel.id}/",
            {"caption": "updated caption"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.reel.refresh_from_db()
        self.assertEqual(self.reel.caption, "updated caption")

    def test_processing_status_not_writable_via_patch(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.patch(
            f"/api/v1/reels/{self.reel.id}/",
            {"processing_status": "ready"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.reel.refresh_from_db()
        self.assertEqual(self.reel.processing_status, "uploaded")

    def test_status_not_writable_via_patch(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.patch(
            f"/api/v1/reels/{self.reel.id}/",
            {"status": "published"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.reel.refresh_from_db()
        self.assertEqual(self.reel.status, "pending_review")

    def test_owner_delete_is_soft_delete(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.delete(f"/api/v1/reels/{self.reel.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.reel.refresh_from_db()
        self.assertTrue(self.reel.is_deleted)

    def test_unauthenticated_patch_rejected(self):
        response = self.client.patch(
            f"/api/v1/reels/{self.reel.id}/", {"caption": "x"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unauthenticated_delete_rejected(self):
        response = self.client.delete(f"/api/v1/reels/{self.reel.id}/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        


# ---------------------------------------------------------------------------
# Part P-044: rejection_reason field on PostSerializer/ReelSerializer.
# ---------------------------------------------------------------------------


from moderation.services import reject as moderation_reject  # noqa: E402


class TestPostRejectionReason(APITestCase):
    def setUp(self):
        self.user, self.business = _make_business_user(
            "trader-p044-post@example.com", "Trader P044 Post"
        )
        self.moderator = _make_customer("moderator-p044-post@example.com")
        self.moderator.groups.add(Group.objects.get(name="Moderator"))
        self.client.force_authenticate(self.user)
        create_response = self.client.post(
            "/api/v1/posts/", {"caption": "will be rejected"}, format="json"
        )
        self.post_id = create_response.data["id"]

    def test_rejection_reason_is_null_while_pending(self):
        response = self.client.get(f"/api/v1/posts/{self.post_id}/")
        self.assertIsNone(response.data["rejection_reason"])

    def test_rejection_reason_shows_real_text_after_reject(self):
        queue_item = ModerationQueue.objects.get(
            content_type=ContentType.objects.get_for_model(Post),
            object_id=self.post_id,
        )
        moderation_reject(
            queue_item, reviewer=self.moderator, reason="Blurry image, resubmit"
        )

        response = self.client.get(f"/api/v1/posts/{self.post_id}/")
        self.assertEqual(response.data["status"], "rejected")
        self.assertEqual(
            response.data["rejection_reason"], "Blurry image, resubmit"
        )

    def test_rejection_reason_is_null_after_approve(self):
        queue_item = ModerationQueue.objects.get(
            content_type=ContentType.objects.get_for_model(Post),
            object_id=self.post_id,
        )
        from moderation.services import approve as moderation_approve

        moderation_approve(queue_item, reviewer=self.moderator)

        response = self.client.get(f"/api/v1/posts/{self.post_id}/")
        self.assertEqual(response.data["status"], "published")
        self.assertIsNone(response.data["rejection_reason"])


class TestReelRejectionReason(APITestCase):
    def setUp(self):
        self.user, self.business = _make_business_user(
            "trader-p044-reel@example.com", "Trader P044 Reel"
        )
        self.moderator = _make_customer("moderator-p044-reel@example.com")
        self.moderator.groups.add(Group.objects.get(name="Moderator"))

    @patch("content.tasks.transcode_reel.delay")
    def test_rejection_reason_shows_real_text_after_reel_rejected(self, mock_delay):
        self.client.force_authenticate(self.user)
        create_response = self.client.post(
            "/api/v1/reels/",
            {"caption": "reel to reject", "video": _make_video_upload()},
            format="multipart",
        )
        reel_id = create_response.data["id"]

        # Reel does not auto-enqueue on create (P-042) — simulate the
        # transcode task reaching "ready" and creating the queue row,
        # exactly as content/tasks.py::transcode_reel does for real.
        reel = Reel.objects.get(pk=reel_id)
        reel.processing_status = "ready"
        reel.save()
        queue_item = ModerationQueue.objects.create(
            content_type=ContentType.objects.get_for_model(Reel),
            object_id=reel_id,
        )

        moderation_reject(
            queue_item, reviewer=self.moderator, reason="Video too dark"
        )

        response = self.client.get(f"/api/v1/reels/{reel_id}/")
        self.assertEqual(response.data["status"], "rejected")
        self.assertEqual(response.data["rejection_reason"], "Video too dark")