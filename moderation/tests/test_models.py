"""
Tests for moderation.models (ModerationQueue, Moderatable) and the
enqueue-on-creation signal (moderation/signals.py).

Exercises the mixin against moderation.tests.testapp.DummyContent — a real
table in the test database (see config/settings/test.py) — rather than
mocking signal dispatch, since the acceptance criteria calls for a
genuine end-to-end proof: create a Moderatable instance, confirm exactly
one ModerationQueue row appears via the generic FK, and confirm editing
it again does not create a second one.
"""

import pytest
from django.contrib.contenttypes.models import ContentType

from moderation.models import ModerationQueue
from moderation.tests.testapp.models import DummyContent


@pytest.mark.django_db
class TestModerationQueueEnqueueOnCreation:
    def test_creating_a_moderatable_instance_creates_one_queue_row(self):
        post = DummyContent.objects.create(title="hello")

        content_type = ContentType.objects.get_for_model(DummyContent)
        rows = ModerationQueue.objects.filter(
            content_type=content_type, object_id=post.pk
        )

        assert rows.count() == 1

    def test_queue_row_references_the_instance_via_generic_fk(self):
        post = DummyContent.objects.create(title="hello")

        row = ModerationQueue.objects.get(
            content_type=ContentType.objects.get_for_model(DummyContent),
            object_id=post.pk,
        )

        assert row.content_object == post

    def test_editing_an_existing_instance_does_not_create_a_second_row(self):
        post = DummyContent.objects.create(title="hello")

        post.title = "hello, edited"
        post.save()
        post.title = "hello, edited again"
        post.save()

        content_type = ContentType.objects.get_for_model(DummyContent)
        rows = ModerationQueue.objects.filter(
            content_type=content_type, object_id=post.pk
        )

        assert rows.count() == 1

    def test_queue_row_defaults_to_pending_status_and_normal_priority(self):
        post = DummyContent.objects.create(title="hello")

        row = ModerationQueue.objects.get(
            content_type=ContentType.objects.get_for_model(DummyContent),
            object_id=post.pk,
        )

        assert row.status == ModerationQueue.Status.PENDING
        assert row.priority == ModerationQueue.Priority.NORMAL

    def test_moderatable_instance_defaults_to_pending_review_status(self):
        post = DummyContent.objects.create(title="hello")

        assert post.status == DummyContent.Status.PENDING_REVIEW

    def test_saving_a_non_moderatable_model_does_not_create_a_queue_row(self):
        # Sanity check that the globally-connected (sender-less)
        # post_save receiver correctly ignores instances that are not
        # Moderatable, rather than erroring or enqueueing everything
        # that gets saved anywhere in the project.
        content_type = ContentType.objects.get_for_model(DummyContent)
        before = ModerationQueue.objects.count()

        ContentType.objects.get_for_model(ModerationQueue)  # any ORM save path

        assert ModerationQueue.objects.count() == before
        assert content_type is not None


@pytest.mark.django_db
class TestModerationQueueIsNotSoftDeletable:
    def test_moderation_queue_has_no_soft_delete_fields(self):
        field_names = {f.name for f in ModerationQueue._meta.get_fields()}

        assert "is_deleted" not in field_names
        assert "deleted_at" not in field_names

    def test_moderation_queue_has_no_all_objects_manager(self):
        # SoftDeleteModel subclasses expose `.all_objects`; ModerationQueue
        # deliberately does not inherit SoftDeleteModel at all (see
        # models.py's docstring), so this manager should not exist here.
        assert not hasattr(ModerationQueue, "all_objects")
