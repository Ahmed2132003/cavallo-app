from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from businesses.services import create_business_profile
from content.models import Post, Reel
from django.contrib.auth import get_user_model
from moderation.models import ModerationQueue

User = get_user_model()


class TestPostModel(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="model-test@example.com",
            email="model-test@example.com",
            password="testpass123",
            account_type="business",
        )
        self.business = create_business_profile(
            user=self.user,
            business_name="Model Test Trader",
            business_type="trader",
            country="EG",
            city="Ismailia",
        )

    def test_new_post_defaults_to_pending_review(self):
        post = Post.objects.create(business=self.business, caption="hi")
        self.assertEqual(post.status, "pending_review")

    def test_creating_post_creates_exactly_one_moderation_queue_row(self):
        post = Post.objects.create(business=self.business, caption="hi")
        count = ModerationQueue.objects.filter(
            content_type=ContentType.objects.get_for_model(Post),
            object_id=post.id,
        ).count()
        self.assertEqual(count, 1)

    def test_editing_post_does_not_create_additional_queue_rows(self):
        post = Post.objects.create(business=self.business, caption="hi")
        post.caption = "edited"
        post.save()
        post.caption = "edited again"
        post.save()
        count = ModerationQueue.objects.filter(
            content_type=ContentType.objects.get_for_model(Post),
            object_id=post.id,
        ).count()
        self.assertEqual(count, 1)

    def test_get_moderation_preview_returns_real_data(self):
        post = Post.objects.create(
            business=self.business, caption="a real caption here"
        )
        preview = post.get_moderation_preview()
        self.assertEqual(
            preview, {"preview_text": "a real caption here", "preview_image_url": None}
        )

    def test_get_moderation_preview_truncates_long_caption(self):
        long_caption = "x" * 300
        post = Post.objects.create(business=self.business, caption=long_caption)
        preview = post.get_moderation_preview()
        self.assertEqual(len(preview["preview_text"]), 200)


class TestReelModel(TestCase):
    """
    Part P-042. The two tests that matter most here are
    `test_creating_reel_does_not_create_a_moderation_queue_row` and
    `test_editing_reel_still_does_not_create_a_queue_row` — they are the
    model-level proof of the whole point of this part: a Reel must NOT
    enter moderation on creation, unlike Post. The Celery-task side
    (creating the queue row once processing_status == "ready") is
    exercised separately once content/tasks.py exists.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="reel-model-test@example.com",
            email="reel-model-test@example.com",
            password="testpass123",
            account_type="business",
        )
        self.business = create_business_profile(
            user=self.user,
            business_name="Model Test Trader (Reel)",
            business_type="trader",
            country="EG",
            city="Ismailia",
        )

    def _make_reel(self, **kwargs):
        kwargs.setdefault("business", self.business)
        kwargs.setdefault("caption", "hi")
        kwargs.setdefault(
            "video",
            SimpleUploadedFile(
                "raw.mp4", b"not-a-real-video-just-bytes", content_type="video/mp4"
            ),
        )
        return Reel.objects.create(**kwargs)

    def test_new_reel_defaults_to_processing_status_uploaded(self):
        reel = self._make_reel()
        self.assertEqual(reel.processing_status, Reel.ProcessingStatus.UPLOADED)

    def test_new_reel_defaults_to_pending_review_moderation_status(self):
        # Moderatable's own status field default must be completely
        # unaffected by the deferred-enqueue hook — only the QUEUE side
        # effect is deferred, not the content's own status value.
        reel = self._make_reel()
        self.assertEqual(reel.status, "pending_review")

    def test_creating_reel_does_not_create_a_moderation_queue_row(self):
        reel = self._make_reel()
        count = ModerationQueue.objects.filter(
            content_type=ContentType.objects.get_for_model(Reel),
            object_id=reel.id,
        ).count()
        self.assertEqual(count, 0)

    def test_editing_reel_still_does_not_create_a_queue_row(self):
        reel = self._make_reel()
        reel.caption = "edited"
        reel.save()
        reel.processing_status = Reel.ProcessingStatus.PROCESSING
        reel.save()
        count = ModerationQueue.objects.filter(
            content_type=ContentType.objects.get_for_model(Reel),
            object_id=reel.id,
        ).count()
        self.assertEqual(count, 0)

    def test_get_moderation_preview_returns_real_data_with_no_thumbnail_yet(self):
        reel = self._make_reel(caption="a real caption here")
        preview = reel.get_moderation_preview()
        self.assertEqual(
            preview, {"preview_text": "a real caption here", "preview_image_url": None}
        )

    def test_get_moderation_preview_uses_real_thumbnail_url_once_set(self):
        reel = self._make_reel(caption="a real caption here")
        reel.thumbnail = SimpleUploadedFile(
            "thumb.jpg", b"not-a-real-jpeg", content_type="image/jpeg"
        )
        reel.save()
        preview = reel.get_moderation_preview()
        self.assertEqual(preview["preview_text"], "a real caption here")
        self.assertIn("thumb", preview["preview_image_url"])

    def test_get_moderation_preview_truncates_long_caption(self):
        long_caption = "x" * 300
        reel = self._make_reel(caption=long_caption)
        preview = reel.get_moderation_preview()
        self.assertEqual(len(preview["preview_text"]), 200)