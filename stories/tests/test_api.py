from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
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
