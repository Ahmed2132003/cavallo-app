"""
Tests for stories.tasks.expire_stale_stories (Part P-048).

Runs the task as a plain function call (Celery's shared_task decorator
makes it directly callable without a worker) — same convention as
moderation/tests/test_tasks.py (Part P-039). Uses QuerySet.update() to
backdate published_at/expires_at (and, for test setup only, status),
bypassing Story.save()'s "only ever set on first creation" guard (see
stories/models.py) and moderation.services' state machine respectively.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from businesses.services import create_business_profile
from stories.models import Story
from stories.tasks import expire_stale_stories

User = get_user_model()


def _make_business():
    user = User.objects.create_user(
        username="story-task-trader@example.com",
        email="story-task-trader@example.com",
        password="testpass123",
        account_type="business",
    )
    return create_business_profile(
        user=user,
        business_name="Story Task Test Trader",
        business_type="trader",
        country="EG",
        city="Ismailia",
    )


def _make_story(business, *, expires_delta, status=Story.Status.PUBLISHED):
    """
    Create a Story then backdate published_at/expires_at (and set
    status) via QuerySet.update() — bypasses Story.save()'s
    first-creation-only guard. Setting status directly here is test
    setup ONLY, a deliberate bypass of the real
    moderation.services.approve()/reject() state machine; never done
    this way in application code.
    """
    story = Story.objects.create(
        business=business,
        media=SimpleUploadedFile(
            "story.jpg", b"not-a-real-image-just-bytes", content_type="image/jpeg"
        ),
    )
    now = timezone.now()
    Story.objects.filter(pk=story.pk).update(
        status=status,
        published_at=now - timedelta(hours=24) + expires_delta,
        expires_at=now + expires_delta,
    )
    story.refresh_from_db()
    return story


@pytest.mark.django_db
class TestExpireStaleStoriesArchival:
    def test_expired_story_gets_archived_at_set(self):
        business = _make_business()
        story = _make_story(business, expires_delta=timedelta(seconds=-1))

        result = expire_stale_stories()

        story.refresh_from_db()
        assert result["archived_count"] == 1
        assert story.archived_at is not None

    def test_not_yet_expired_story_is_left_untouched(self):
        business = _make_business()
        story = _make_story(business, expires_delta=timedelta(hours=1))

        result = expire_stale_stories()

        story.refresh_from_db()
        assert result["archived_count"] == 0
        assert story.archived_at is None

    def test_archiving_does_not_change_status_or_is_deleted(self):
        """
        Core Definition-of-Done requirement: the sweep job is pure
        bookkeeping. An expired-but-still-published Story stays
        status='published' and is_deleted=False after archiving —
        archived_at is the ONLY field this task ever writes.
        """
        business = _make_business()
        story = _make_story(
            business,
            expires_delta=timedelta(seconds=-1),
            status=Story.Status.PUBLISHED,
        )

        expire_stale_stories()

        story.refresh_from_db()
        assert story.status == Story.Status.PUBLISHED
        assert story.is_deleted is False

    def test_already_archived_story_is_not_reprocessed(self):
        business = _make_business()
        story = _make_story(business, expires_delta=timedelta(seconds=-1))
        first_result = expire_stale_stories()
        story.refresh_from_db()
        first_archived_at = story.archived_at
        assert first_result["archived_count"] == 1

        second_result = expire_stale_stories()

        story.refresh_from_db()
        assert second_result["archived_count"] == 0
        assert story.archived_at == first_archived_at


@pytest.mark.django_db
class TestExpireStaleStoriesIdempotency:
    def test_running_twice_in_a_row_is_a_no_op_the_second_time(self):
        business = _make_business()
        _make_story(business, expires_delta=timedelta(seconds=-1))
        _make_story(business, expires_delta=timedelta(seconds=-30))

        first_result = expire_stale_stories()
        second_result = expire_stale_stories()

        assert first_result == {"archived_count": 2}
        assert second_result == {"archived_count": 0}

    def test_running_with_nothing_expired_is_a_no_op(self):
        business = _make_business()
        _make_story(business, expires_delta=timedelta(hours=1))

        result = expire_stale_stories()

        assert result == {"archived_count": 0}
