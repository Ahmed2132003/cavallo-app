from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from businesses.services import create_business_profile
from core.tests.test_media import _DISGUISED_EXE_BYTES, _VALID_PNG_BYTES
from moderation.models import ModerationQueue
from stories.models import Story

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


class TestStoryCreate(APITestCase):
    def setUp(self):
        self.user, self.business = _make_business_user(
            "story-trader-a@example.com", "Story Trader A"
        )

    def test_create_story_auto_enqueues_moderation_with_fast_path_priority(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/stories/",
            {
                "media": SimpleUploadedFile(
                    "story.png", _VALID_PNG_BYTES, content_type="image/png"
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        row = ModerationQueue.objects.get(
            content_type=ContentType.objects.get_for_model(Story),
            object_id=response.data["id"],
        )
        self.assertEqual(row.priority, ModerationQueue.Priority.FAST_PATH)

    def test_business_field_in_body_is_ignored_not_honored(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/stories/",
            {
                "media": SimpleUploadedFile(
                    "story.png", _VALID_PNG_BYTES, content_type="image/png"
                ),
                "business": 999999,
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        story = Story.objects.get(pk=response.data["id"])
        self.assertEqual(story.business_id, self.business.id)

    def test_status_in_body_is_ignored_on_create(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/stories/",
            {
                "media": SimpleUploadedFile(
                    "story.png", _VALID_PNG_BYTES, content_type="image/png"
                ),
                "status": "published",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        story = Story.objects.get(pk=response.data["id"])
        self.assertEqual(story.status, "pending_review")

    def test_expires_at_in_response_is_24_hours_after_published_at(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/stories/",
            {
                "media": SimpleUploadedFile(
                    "story.png", _VALID_PNG_BYTES, content_type="image/png"
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        story = Story.objects.get(pk=response.data["id"])
        self.assertEqual(story.expires_at - story.published_at, timedelta(hours=24))

    def test_disguised_exe_upload_is_rejected(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/stories/",
            {
                "media": SimpleUploadedFile(
                    "story.png", _DISGUISED_EXE_BYTES, content_type="image/png"
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unauthenticated_create_rejected(self):
        response = self.client.post(
            "/api/v1/stories/",
            {
                "media": SimpleUploadedFile(
                    "story.png", _VALID_PNG_BYTES, content_type="image/png"
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_without_business_profile_gets_403_on_create(self):
        customer = _make_customer("story-customer-a@example.com")
        self.client.force_authenticate(customer)
        response = self.client.post(
            "/api/v1/stories/",
            {
                "media": SimpleUploadedFile(
                    "story.png", _VALID_PNG_BYTES, content_type="image/png"
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TestStoryOwnList(APITestCase):
    def setUp(self):
        self.user_a, self.business_a = _make_business_user(
            "story-trader-a2@example.com", "Story Trader A2"
        )
        self.user_b, self.business_b = _make_business_user(
            "story-trader-b@example.com", "Story Trader B"
        )
        self.story_a = Story.objects.create(
            business=self.business_a,
            media=SimpleUploadedFile(
                "a.png", _VALID_PNG_BYTES, content_type="image/png"
            ),
        )
        self.story_b = Story.objects.create(
            business=self.business_b,
            media=SimpleUploadedFile(
                "b.png", _VALID_PNG_BYTES, content_type="image/png"
            ),
        )

    def test_own_list_excludes_other_business_stories(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.get("/api/v1/stories/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.story_a.id, ids)
        self.assertNotIn(self.story_b.id, ids)

    def test_customer_without_business_profile_sees_empty_list(self):
        customer = _make_customer("story-customer-b@example.com")
        self.client.force_authenticate(customer)
        response = self.client.get("/api/v1/stories/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], [])

    def test_unauthenticated_list_rejected(self):
        response = self.client.get("/api/v1/stories/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class TestStoryPublicList(APITestCase):
    """
    Part P-048. Proves StoryPublicListView's queryset condition
    (status='published' AND expires_at__gt=now()) is the real,
    live visibility gate — genuinely independent of
    stories.tasks.expire_stale_stories(), which is never imported or
    called anywhere in this class.
    """

    def setUp(self):
        self.user, self.business = _make_business_user(
            "story-public-trader@example.com", "Story Public Trader"
        )

    def _make_story(self, *, status_value, expires_delta):
        """
        Create a Story then backdate published_at/expires_at (and set
        status) via QuerySet.update() — bypasses Story.save()'s
        first-creation-only guard. Setting status directly is test
        setup ONLY (a deliberate bypass of the real
        moderation.services state machine), with a clear comment per
        this part's own testing instructions.
        """
        story = Story.objects.create(
            business=self.business,
            media=SimpleUploadedFile(
                "story.png", _VALID_PNG_BYTES, content_type="image/png"
            ),
        )
        now = timezone.now()
        Story.objects.filter(pk=story.pk).update(
            status=status_value,
            published_at=now - timedelta(hours=24) + expires_delta,
            expires_at=now + expires_delta,
        )
        story.refresh_from_db()
        return story

    def test_published_not_yet_expired_story_is_visible(self):
        story = self._make_story(
            status_value=Story.Status.PUBLISHED, expires_delta=timedelta(hours=1)
        )
        response = self.client.get("/api/v1/stories/public/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(story.id, ids)

    def test_expired_published_story_is_invisible_without_running_sweep_job(self):
        """
        THE CRITICAL TEST (Part P-048's own Definition of Done):
        create a published Story, manually backdate its expires_at
        into the past via a direct .update() call, WITHOUT ever
        calling stories.tasks.expire_stale_stories(), and confirm the
        public endpoint already excludes it. This proves visibility is
        genuinely query-driven — evaluated fresh on this exact
        request — and NOT dependent on the sweep job having run.
        """
        expired_story = self._make_story(
            status_value=Story.Status.PUBLISHED, expires_delta=timedelta(seconds=-1)
        )

        response = self.client.get("/api/v1/stories/public/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(expired_story.id, ids)
        # archived_at was never touched — proves invisibility came from
        # the query condition itself, not from any bookkeeping field.
        expired_story.refresh_from_db()
        self.assertIsNone(expired_story.archived_at)

    def test_pending_review_story_is_never_public_even_if_not_expired(self):
        story = self._make_story(
            status_value=Story.Status.PENDING_REVIEW,
            expires_delta=timedelta(hours=1),
        )
        response = self.client.get("/api/v1/stories/public/")
        ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(story.id, ids)

    def test_rejected_story_is_never_public_even_if_not_expired(self):
        story = self._make_story(
            status_value=Story.Status.REJECTED, expires_delta=timedelta(hours=1)
        )
        response = self.client.get("/api/v1/stories/public/")
        ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(story.id, ids)

    def test_unauthenticated_request_is_allowed(self):
        self._make_story(
            status_value=Story.Status.PUBLISHED, expires_delta=timedelta(hours=1)
        )
        response = self.client.get("/api/v1/stories/public/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_business_id_filter_excludes_other_businesses_stories(self):
        _, other_business = _make_business_user(
            "story-public-trader-2@example.com", "Story Public Trader 2"
        )
        mine = self._make_story(
            status_value=Story.Status.PUBLISHED, expires_delta=timedelta(hours=1)
        )
        other_story = Story.objects.create(
            business=other_business,
            media=SimpleUploadedFile(
                "other.png", _VALID_PNG_BYTES, content_type="image/png"
            ),
        )
        Story.objects.filter(pk=other_story.pk).update(
            status=Story.Status.PUBLISHED,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        response = self.client.get(
            f"/api/v1/stories/public/?business_id={self.business.id}"
        )

        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(mine.id, ids)
        self.assertNotIn(other_story.id, ids)
