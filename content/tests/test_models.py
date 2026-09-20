from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from businesses.services import create_business_profile
from content.models import Post
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