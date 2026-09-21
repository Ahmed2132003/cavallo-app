"""
Part P-043 — Central Published-Content Manager: public endpoint tests.

Covers the explicit acceptance-criteria matrix from this part's own
spec: a pending_review or rejected Post/Reel never appears via the
public endpoints, an approved (published) one does, and an approved-
but-soft-deleted one does not — for both Post and Reel, plus the
Reel-only extra processing_status="ready" condition, the ?business_id=
filter, and proof that the owner-facing endpoints (PostListCreateView/
ReelListCreateView, still built on the default .objects manager) are
completely unaffected by this part.
"""

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from businesses.services import create_business_profile
from content.models import Post, Reel

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


class TestPostPublicList(APITestCase):
    def setUp(self):
        self.user, self.business = _make_business_user(
            "trader-pub-post@example.com", "Trader Pub Post"
        )
        self.pending = Post.objects.create(
            business=self.business, caption="pending", status="pending_review"
        )
        self.rejected = Post.objects.create(
            business=self.business, caption="rejected", status="rejected"
        )
        self.published = Post.objects.create(
            business=self.business, caption="published", status="published"
        )
        self.soft_deleted_published = Post.objects.create(
            business=self.business,
            caption="published then soft-deleted",
            status="published",
        )
        self.soft_deleted_published.delete()  # soft delete, is_deleted=True

    def _public_ids(self):
        response = self.client.get("/api/v1/posts/public/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [item["id"] for item in response.data["results"]]

    def test_pending_post_not_in_public_list(self):
        self.assertNotIn(self.pending.id, self._public_ids())

    def test_rejected_post_not_in_public_list(self):
        self.assertNotIn(self.rejected.id, self._public_ids())

    def test_published_post_in_public_list(self):
        self.assertIn(self.published.id, self._public_ids())

    def test_soft_deleted_published_post_not_in_public_list(self):
        self.assertNotIn(self.soft_deleted_published.id, self._public_ids())

    def test_public_list_requires_no_auth(self):
        response = self.client.get("/api/v1/posts/public/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_public_serializer_omits_status_field(self):
        response = self.client.get("/api/v1/posts/public/")
        item = next(
            entry
            for entry in response.data["results"]
            if entry["id"] == self.published.id
        )
        self.assertNotIn("status", item)


class TestPostPublicListBusinessIdFilter(APITestCase):
    def setUp(self):
        self.user_a, self.business_a = _make_business_user(
            "trader-pub-post-a@example.com", "Trader Pub Post A"
        )
        self.user_b, self.business_b = _make_business_user(
            "trader-pub-post-b@example.com", "Trader Pub Post B"
        )
        self.post_a = Post.objects.create(
            business=self.business_a, caption="A's post", status="published"
        )
        self.post_b = Post.objects.create(
            business=self.business_b, caption="B's post", status="published"
        )

    def test_business_id_filter_narrows_results(self):
        response = self.client.get(
            f"/api/v1/posts/public/?business_id={self.business_a.id}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.post_a.id, ids)
        self.assertNotIn(self.post_b.id, ids)


class TestPostOwnEndpointsUnaffectedByPublishedManager(APITestCase):
    """
    Proves PostListCreateView/PostDetailView still use the default
    (unfiltered) .objects manager, unchanged by this part — the
    owner's own list must still show pending/rejected content, and the
    existing public detail-by-id route must still work for any status.
    """

    def setUp(self):
        self.user, self.business = _make_business_user(
            "trader-pub-post-own@example.com", "Trader Pub Post Own"
        )
        self.pending = Post.objects.create(
            business=self.business, caption="pending", status="pending_review"
        )
        self.rejected = Post.objects.create(
            business=self.business, caption="rejected", status="rejected"
        )

    def test_owner_own_list_still_includes_pending_and_rejected(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/posts/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.pending.id, ids)
        self.assertIn(self.rejected.id, ids)

    def test_existing_detail_route_still_returns_pending_item_to_anyone(self):
        # PostDetailView's GET was already public-by-id since P-041 —
        # this part must not have narrowed it to published_objects.
        response = self.client.get(f"/api/v1/posts/{self.pending.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)


def _make_video_upload(name="raw.mp4"):
    import os

    from django.core.files.uploadedfile import SimpleUploadedFile

    fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
    with open(os.path.join(fixtures_dir, "small_test_reel.mp4"), "rb") as f:
        video_bytes = f.read()
    return SimpleUploadedFile(name, video_bytes, content_type="video/mp4")


class TestReelPublicList(APITestCase):
    def setUp(self):
        self.user, self.business = _make_business_user(
            "trader-pub-reel@example.com", "Trader Pub Reel"
        )
        self.pending = Reel.objects.create(
            business=self.business,
            caption="pending",
            video=_make_video_upload("pending.mp4"),
            status="pending_review",
            processing_status="ready",
        )
        self.rejected = Reel.objects.create(
            business=self.business,
            caption="rejected",
            video=_make_video_upload("rejected.mp4"),
            status="rejected",
            processing_status="ready",
        )
        self.published = Reel.objects.create(
            business=self.business,
            caption="published",
            video=_make_video_upload("published.mp4"),
            status="published",
            processing_status="ready",
        )
        self.soft_deleted_published = Reel.objects.create(
            business=self.business,
            caption="published then soft-deleted",
            video=_make_video_upload("soft-deleted.mp4"),
            status="published",
            processing_status="ready",
        )
        self.soft_deleted_published.delete()
        # Belt-and-suspenders case: approved but still processing
        # (should never happen in normal flow — see
        # ReelPublishedManager's docstring — but must still be excluded).
        self.approved_but_not_ready = Reel.objects.create(
            business=self.business,
            caption="approved but not ready",
            video=_make_video_upload("not-ready.mp4"),
            status="published",
            processing_status="processing",
        )

    def _public_ids(self):
        response = self.client.get("/api/v1/reels/public/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [item["id"] for item in response.data["results"]]

    def test_pending_reel_not_in_public_list(self):
        self.assertNotIn(self.pending.id, self._public_ids())

    def test_rejected_reel_not_in_public_list(self):
        self.assertNotIn(self.rejected.id, self._public_ids())

    def test_published_reel_in_public_list(self):
        self.assertIn(self.published.id, self._public_ids())

    def test_soft_deleted_published_reel_not_in_public_list(self):
        self.assertNotIn(self.soft_deleted_published.id, self._public_ids())

    def test_approved_but_not_ready_reel_not_in_public_list(self):
        self.assertNotIn(self.approved_but_not_ready.id, self._public_ids())

    def test_public_serializer_omits_status_and_processing_status_fields(self):
        response = self.client.get("/api/v1/reels/public/")
        item = next(
            entry
            for entry in response.data["results"]
            if entry["id"] == self.published.id
        )
        self.assertNotIn("status", item)
        self.assertNotIn("processing_status", item)


class TestReelPublicListBusinessIdFilter(APITestCase):
    def setUp(self):
        self.user_a, self.business_a = _make_business_user(
            "trader-pub-reel-a@example.com", "Trader Pub Reel A"
        )
        self.user_b, self.business_b = _make_business_user(
            "trader-pub-reel-b@example.com", "Trader Pub Reel B"
        )
        self.reel_a = Reel.objects.create(
            business=self.business_a,
            caption="A's reel",
            video=_make_video_upload("a.mp4"),
            status="published",
            processing_status="ready",
        )
        self.reel_b = Reel.objects.create(
            business=self.business_b,
            caption="B's reel",
            video=_make_video_upload("b.mp4"),
            status="published",
            processing_status="ready",
        )

    def test_business_id_filter_narrows_results(self):
        response = self.client.get(
            f"/api/v1/reels/public/?business_id={self.business_a.id}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.reel_a.id, ids)
        self.assertNotIn(self.reel_b.id, ids)


class TestReelOwnEndpointsUnaffectedByPublishedManager(APITestCase):
    def setUp(self):
        self.user, self.business = _make_business_user(
            "trader-pub-reel-own@example.com", "Trader Pub Reel Own"
        )
        self.pending = Reel.objects.create(
            business=self.business,
            caption="pending",
            video=_make_video_upload("own-pending.mp4"),
            status="pending_review",
            processing_status="uploaded",
        )

    def test_owner_own_list_still_includes_pending(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/reels/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.pending.id, ids)

    def test_existing_detail_route_still_returns_pending_item_to_anyone(self):
        response = self.client.get(f"/api/v1/reels/{self.pending.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)