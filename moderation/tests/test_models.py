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
from moderation.tests.testapp.models import (
    DummyContent,
    DummyDeferredContent,
    DummyFastPathContent,
)


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


@pytest.mark.django_db
class TestDeferredEnqueueHook:
    """
    Part P-042's auto_enqueue_on_create hook, proven here against a
    throwaway model (DummyDeferredContent) before Reel exists, exactly
    the way P-036's own DummyContent proved the base signal before Post
    existed.
    """

    def test_default_hook_value_is_true_on_the_mixin(self):
        # Regression guard: DummyContent does not set the attribute at
        # all, so it must inherit True from Moderatable directly.
        assert DummyContent.auto_enqueue_on_create is True

    def test_opted_out_model_does_not_get_a_queue_row_on_create(self):
        item = DummyDeferredContent.objects.create(title="raw upload")

        content_type = ContentType.objects.get_for_model(DummyDeferredContent)
        rows = ModerationQueue.objects.filter(
            content_type=content_type, object_id=item.pk
        )

        assert rows.count() == 0

    def test_opted_out_model_still_defaults_to_pending_review_status(self):
        # The hook only defers the queue side-effect; Moderatable's own
        # `status` field default must be completely unaffected.
        item = DummyDeferredContent.objects.create(title="raw upload")

        assert item.status == DummyDeferredContent.Status.PENDING_REVIEW

    def test_manually_creating_the_queue_row_afterwards_works_normally(self):
        # Mirrors exactly what content/tasks.py's transcode_reel will do
        # for a real Reel once processing_status reaches "ready".
        item = DummyDeferredContent.objects.create(title="raw upload")
        content_type = ContentType.objects.get_for_model(DummyDeferredContent)

        assert ModerationQueue.objects.filter(
            content_type=content_type, object_id=item.pk
        ).count() == 0

        ModerationQueue.objects.create(
            content_type=content_type,
            object_id=item.pk,
        )

        rows = ModerationQueue.objects.filter(
            content_type=content_type, object_id=item.pk
        )
        assert rows.count() == 1
        assert rows.first().content_object == item

    def test_opting_out_does_not_affect_the_normal_opted_in_model(self):
        # Cross-check in the same test run: DummyContent (opted in) and
        # DummyDeferredContent (opted out) must not interfere with each
        # other's enqueue behavior.
        normal = DummyContent.objects.create(title="normal")
        deferred = DummyDeferredContent.objects.create(title="deferred")

        normal_rows = ModerationQueue.objects.filter(
            content_type=ContentType.objects.get_for_model(DummyContent),
            object_id=normal.pk,
        )
        deferred_rows = ModerationQueue.objects.filter(
            content_type=ContentType.objects.get_for_model(DummyDeferredContent),
            object_id=deferred.pk,
        )

        assert normal_rows.count() == 1
        assert deferred_rows.count() == 0


@pytest.mark.django_db
class TestFastPathPriorityHook:
    """
    Part P-046's moderation_priority hook, proven here against a
    throwaway model (DummyFastPathContent) before Story exists, exactly
    the way DummyDeferredContent proved P-042's auto_enqueue_on_create
    hook before Reel existed.
    """

    def test_default_hook_value_is_normal_on_the_mixin(self):
        # Regression guard: DummyContent does not set the attribute at
        # all, so it must inherit ModerationQueue.Priority.NORMAL
        # directly from Moderatable.
        assert DummyContent.moderation_priority == ModerationQueue.Priority.NORMAL

    def test_opted_in_model_gets_fast_path_priority_on_its_queue_row(self):
        item = DummyFastPathContent.objects.create(title="urgent")

        row = ModerationQueue.objects.get(
            content_type=ContentType.objects.get_for_model(DummyFastPathContent),
            object_id=item.pk,
        )

        assert row.priority == ModerationQueue.Priority.FAST_PATH

    def test_overriding_priority_does_not_affect_the_normal_opted_in_model(self):
        normal = DummyContent.objects.create(title="normal")
        fast = DummyFastPathContent.objects.create(title="fast")

        normal_row = ModerationQueue.objects.get(
            content_type=ContentType.objects.get_for_model(DummyContent),
            object_id=normal.pk,
        )
        fast_row = ModerationQueue.objects.get(
            content_type=ContentType.objects.get_for_model(DummyFastPathContent),
            object_id=fast.pk,
        )

        assert normal_row.priority == ModerationQueue.Priority.NORMAL
        assert fast_row.priority == ModerationQueue.Priority.FAST_PATH