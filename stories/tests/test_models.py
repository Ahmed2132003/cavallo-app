from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from businesses.services import create_business_profile
from moderation.models import ModerationQueue
from stories.models import Story

User = get_user_model()


class TestStoryModel(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="story-model-test@example.com",
            email="story-model-test@example.com",
            password="testpass123",
            account_type="business",
        )
        self.business = create_business_profile(
            user=self.user,
            business_name="Story Model Test Trader",
            business_type="trader",
            country="EG",
            city="Ismailia",
        )

    def _make_story(self, **kwargs):
        kwargs.setdefault("business", self.business)
        kwargs.setdefault(
            "media",
            SimpleUploadedFile(
                "story.jpg", b"not-a-real-image-just-bytes", content_type="image/jpeg"
            ),
        )
        return Story.objects.create(**kwargs)

    def test_new_story_defaults_to_pending_review(self):
        story = self._make_story()
        self.assertEqual(story.status, "pending_review")

    def test_creating_story_creates_exactly_one_moderation_queue_row(self):
        story = self._make_story()
        count = ModerationQueue.objects.filter(
            content_type=ContentType.objects.get_for_model(Story),
            object_id=story.id,
        ).count()
        self.assertEqual(count, 1)

    def test_creating_story_queue_row_has_fast_path_priority(self):
        """
        This is the acceptance criterion this part exists to prove:
        Story deviates from Post/Reel's default 'normal' priority.
        """
        story = self._make_story()
        row = ModerationQueue.objects.get(
            content_type=ContentType.objects.get_for_model(Story),
            object_id=story.id,
        )
        self.assertEqual(row.priority, ModerationQueue.Priority.FAST_PATH)

    def test_editing_story_does_not_create_additional_queue_rows(self):
        story = self._make_story()
        story.media = SimpleUploadedFile(
            "story2.jpg", b"different-bytes", content_type="image/jpeg"
        )
        story.save()
        count = ModerationQueue.objects.filter(
            content_type=ContentType.objects.get_for_model(Story),
            object_id=story.id,
        ).count()
        self.assertEqual(count, 1)

    def test_expires_at_is_exactly_24_hours_after_published_at(self):
        """
        Proves the expires_at strategy without a freezegun dependency
        (not present in requirements.txt — not added here without
        being asked). Since save() computes
        expires_at = published_at + timedelta(hours=24) from a single
        timezone.now() call, the 24h delta holds exactly regardless of
        real wall-clock time, which is an equally rigorous proof of
        this specific computation.
        """
        story = self._make_story()
        self.assertEqual(story.expires_at - story.published_at, timedelta(hours=24))

    def test_published_at_and_expires_at_are_not_recomputed_on_subsequent_saves(self):
        story = self._make_story()
        original_published_at = story.published_at
        original_expires_at = story.expires_at

        story.media = SimpleUploadedFile(
            "story3.jpg", b"more-different-bytes", content_type="image/jpeg"
        )
        story.save()
        story.refresh_from_db()

        self.assertEqual(story.published_at, original_published_at)
        self.assertEqual(story.expires_at, original_expires_at)

    def test_story_has_no_comments_field_or_relation(self):
        field_names = {f.name for f in Story._meta.get_fields()}
        self.assertNotIn("comments", field_names)
        self.assertNotIn("comment_set", field_names)

    def test_composite_index_on_business_status_expires_at_exists(self):
        index_field_sets = [tuple(idx.fields) for idx in Story._meta.indexes]
        self.assertIn(("business", "status", "expires_at"), index_field_sets)

    def test_get_moderation_preview_returns_business_name_and_media_url(self):
        story = self._make_story()
        preview = story.get_moderation_preview()
        self.assertEqual(
            preview["preview_text"], f"Story by {self.business.business_name}"
        )
        self.assertIn("story", preview["preview_image_url"])