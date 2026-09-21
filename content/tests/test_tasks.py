"""
Tests for content.tasks.transcode_reel (Part P-042).

Runs the task as a plain function call (same convention as
moderation/tests/test_tasks.py for P-039 — Celery's task decorator
makes it directly callable without a worker). The real ffmpeg/ffprobe
binaries genuinely run against a real committed video fixture — never
mocked, per this part's own acceptance criteria (a mocked subprocess
call would not catch a real command-syntax bug). Reel.video/thumbnail
genuinely round-trip through the real MediaStorage backend (P-013,
MinIO in dev/test), same "against the real stack" philosophy as every
prior part.
"""

import os

import pytest
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from businesses.services import create_business_profile
from content.models import Reel
from content.tasks import transcode_reel
from moderation.models import ModerationQueue

User = get_user_model()

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
REAL_VIDEO_FIXTURE = os.path.join(FIXTURES_DIR, "small_test_reel.mp4")


@pytest.mark.django_db
class TestTranscodeReelSuccess:
    def setUp_business(self):
        user = User.objects.create_user(
            username="reel-task-test@example.com",
            email="reel-task-test@example.com",
            password="testpass123",
            account_type="business",
        )
        return create_business_profile(
            user=user,
            business_name="Task Test Trader",
            business_type="trader",
            country="EG",
            city="Ismailia",
        )

    def _make_uploaded_reel(self, business):
        with open(REAL_VIDEO_FIXTURE, "rb") as f:
            video = SimpleUploadedFile("raw.mp4", f.read(), content_type="video/mp4")
        return Reel.objects.create(business=business, caption="hi", video=video)

    def test_no_queue_row_exists_before_the_task_runs(self):
        business = self.setUp_business()
        reel = self._make_uploaded_reel(business)

        count = ModerationQueue.objects.filter(
            content_type=ContentType.objects.get_for_model(Reel),
            object_id=reel.pk,
        ).count()
        assert count == 0

    def test_real_video_transcodes_to_ready_with_thumbnail_and_duration(self):
        business = self.setUp_business()
        reel = self._make_uploaded_reel(business)

        transcode_reel(reel.id)

        reel.refresh_from_db()
        assert reel.processing_status == Reel.ProcessingStatus.READY
        assert reel.thumbnail
        assert reel.duration_seconds is not None
        # The fixture is a 3-second clip; ffprobe's real measurement
        # should land close to that (allow encoder rounding slack).
        assert 1 <= reel.duration_seconds <= 5

    def test_exactly_one_queue_row_exists_after_success(self):
        business = self.setUp_business()
        reel = self._make_uploaded_reel(business)

        transcode_reel(reel.id)

        rows = ModerationQueue.objects.filter(
            content_type=ContentType.objects.get_for_model(Reel),
            object_id=reel.pk,
        )
        assert rows.count() == 1
        assert rows.first().content_object == reel

    def test_running_twice_does_not_create_a_second_queue_row(self):
        business = self.setUp_business()
        reel = self._make_uploaded_reel(business)

        transcode_reel(reel.id)
        transcode_reel(reel.id)

        rows = ModerationQueue.objects.filter(
            content_type=ContentType.objects.get_for_model(Reel),
            object_id=reel.pk,
        )
        assert rows.count() == 1


@pytest.mark.django_db
class TestTranscodeReelFailure:
    def setUp_business(self):
        user = User.objects.create_user(
            username="reel-task-fail-test@example.com",
            email="reel-task-fail-test@example.com",
            password="testpass123",
            account_type="business",
        )
        return create_business_profile(
            user=user,
            business_name="Task Fail Test Trader",
            business_type="trader",
            country="EG",
            city="Ismailia",
        )

    def test_corrupt_file_ends_in_failed_with_no_queue_row(self):
        business = self.setUp_business()
        corrupt_video = SimpleUploadedFile(
            "not-really-a-video.mp4",
            b"this is not a real video file, just plain text pretending to be mp4",
            content_type="video/mp4",
        )
        reel = Reel.objects.create(
            business=business, caption="hi", video=corrupt_video
        )

        transcode_reel(reel.id)

        reel.refresh_from_db()
        assert reel.processing_status == Reel.ProcessingStatus.FAILED
        assert reel.duration_seconds is None

        count = ModerationQueue.objects.filter(
            content_type=ContentType.objects.get_for_model(Reel),
            object_id=reel.pk,
        ).count()
        assert count == 0


@pytest.mark.django_db
class TestTranscodeReelMissingReel:
    def test_nonexistent_reel_id_does_not_raise(self):
        # No Reel with this id exists. The task must log and return
        # cleanly, never raise — a stray/late task for an already
        # hard-deleted Reel must not crash a Celery worker.
        transcode_reel(999999999)