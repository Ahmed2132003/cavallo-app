"""
Tests for moderation.services (Part P-037): approve(), reject(), the
double-processing guard, the empty-reason guard, atomicity, and the
ModerationLog audit-trail model.

Uses the same test-only-model harness as P-036's test_models.py:
moderation.tests.testapp.DummyContent, a real table in the test
database. Creating a DummyContent row makes the enqueue signal create
its pending ModerationQueue row, exactly as production content will.

Every "nothing changed" assertion re-queries the database instead of
trusting return values or in-memory objects, so it proves what was
actually persisted.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError

from moderation.models import ModerationLog, ModerationQueue
from moderation.services import AlreadyDecidedError, approve, reject
from moderation.tests.testapp.models import DummyContent


@pytest.fixture
def reviewer(db):
    return get_user_model().objects.create_user(
        username="moderator1", password="pass12345"
    )


@pytest.fixture
def content(db):
    return DummyContent.objects.create(title="needs review")


@pytest.fixture
def queue_item(content):
    return ModerationQueue.objects.get(
        content_type=ContentType.objects.get_for_model(DummyContent),
        object_id=content.pk,
    )


def _fresh_queue_status(queue_item):
    return ModerationQueue.objects.get(pk=queue_item.pk).status


def _fresh_content_status(content):
    return DummyContent.objects.get(pk=content.pk).status


def _log_count(queue_item):
    return ModerationLog.objects.filter(queue_item_id=queue_item.pk).count()


def _assert_untouched(queue_item, content):
    """Queue item still pending, content still pending_review, no log."""
    assert _fresh_queue_status(queue_item) == ModerationQueue.Status.PENDING
    assert _fresh_content_status(content) == DummyContent.Status.PENDING_REVIEW
    assert _log_count(queue_item) == 0


@pytest.mark.django_db
class TestApprove:
    def test_approve_updates_queue_item_and_content_object(
        self, queue_item, content, reviewer
    ):
        approve(queue_item, reviewer)

        assert _fresh_queue_status(queue_item) == ModerationQueue.Status.APPROVED
        assert _fresh_content_status(content) == DummyContent.Status.PUBLISHED

    def test_approve_creates_log_row_with_approved_action(
        self, queue_item, reviewer
    ):
        log = approve(queue_item, reviewer)

        stored = ModerationLog.objects.get(queue_item_id=queue_item.pk)
        assert stored.pk == log.pk
        assert stored.action == ModerationLog.Action.APPROVED
        assert stored.reviewer_id == reviewer.pk
        assert stored.reason == ""
        assert _log_count(queue_item) == 1

    def test_approve_syncs_callers_in_memory_queue_item(
        self, queue_item, reviewer
    ):
        approve(queue_item, reviewer)

        assert queue_item.status == ModerationQueue.Status.APPROVED


@pytest.mark.django_db
class TestReject:
    def test_reject_updates_queue_item_and_content_object(
        self, queue_item, content, reviewer
    ):
        reject(queue_item, reviewer, "Blurry product photos")

        assert _fresh_queue_status(queue_item) == ModerationQueue.Status.REJECTED
        assert _fresh_content_status(content) == DummyContent.Status.REJECTED

    def test_reject_stores_reason_and_reviewer_in_log(
        self, queue_item, reviewer
    ):
        log = reject(queue_item, reviewer, "  Blurry product photos  ")

        stored = ModerationLog.objects.get(queue_item_id=queue_item.pk)
        assert stored.pk == log.pk
        assert stored.action == ModerationLog.Action.REJECTED
        assert stored.reason == "Blurry product photos"
        assert stored.reviewer_id == reviewer.pk
        assert queue_item.status == ModerationQueue.Status.REJECTED


@pytest.mark.django_db
class TestRejectRequiresReason:
    @pytest.mark.parametrize("bad_reason", ["", "   ", None])
    def test_blank_reason_raises_and_changes_nothing(
        self, queue_item, content, reviewer, bad_reason
    ):
        with pytest.raises(ValidationError):
            reject(queue_item, reviewer, bad_reason)

        _assert_untouched(queue_item, content)


@pytest.mark.django_db
class TestDoubleProcessingGuard:
    def test_approving_twice_raises_and_creates_no_second_log(
        self, queue_item, content, reviewer
    ):
        approve(queue_item, reviewer)

        with pytest.raises(AlreadyDecidedError):
            approve(queue_item, reviewer)

        assert _log_count(queue_item) == 1
        assert _fresh_queue_status(queue_item) == ModerationQueue.Status.APPROVED
        assert _fresh_content_status(content) == DummyContent.Status.PUBLISHED

    def test_rejecting_twice_raises_and_creates_no_second_log(
        self, queue_item, content, reviewer
    ):
        reject(queue_item, reviewer, "Not allowed")

        with pytest.raises(AlreadyDecidedError):
            reject(queue_item, reviewer, "Not allowed, again")

        assert _log_count(queue_item) == 1
        assert _fresh_queue_status(queue_item) == ModerationQueue.Status.REJECTED
        assert _fresh_content_status(content) == DummyContent.Status.REJECTED

    def test_rejecting_an_approved_item_raises_and_changes_nothing(
        self, queue_item, content, reviewer
    ):
        approve(queue_item, reviewer)

        with pytest.raises(AlreadyDecidedError):
            reject(queue_item, reviewer, "Changed my mind")

        assert _log_count(queue_item) == 1
        assert _fresh_queue_status(queue_item) == ModerationQueue.Status.APPROVED
        assert _fresh_content_status(content) == DummyContent.Status.PUBLISHED

    def test_approving_a_rejected_item_raises_and_changes_nothing(
        self, queue_item, content, reviewer
    ):
        reject(queue_item, reviewer, "Not allowed")

        with pytest.raises(AlreadyDecidedError):
            approve(queue_item, reviewer)

        assert _log_count(queue_item) == 1
        assert _fresh_queue_status(queue_item) == ModerationQueue.Status.REJECTED
        assert _fresh_content_status(content) == DummyContent.Status.REJECTED

    def test_stale_in_memory_copy_cannot_decide_an_already_decided_item(
        self, queue_item, content, reviewer
    ):
        # Two separate Python objects for the same row, both loaded while
        # it was still pending: this is what a double-tap / two
        # concurrent requests look like. The guard must check the real
        # database row, not the stale in-memory status.
        stale_copy = ModerationQueue.objects.get(pk=queue_item.pk)
        assert stale_copy.status == ModerationQueue.Status.PENDING

        approve(queue_item, reviewer)

        with pytest.raises(AlreadyDecidedError):
            reject(stale_copy, reviewer, "Second reviewer disagrees")

        assert _log_count(queue_item) == 1
        assert _fresh_queue_status(queue_item) == ModerationQueue.Status.APPROVED
        assert _fresh_content_status(content) == DummyContent.Status.PUBLISHED


@pytest.mark.django_db
class TestAtomicity:
    @staticmethod
    def _make_log_creation_fail(monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("simulated failure while creating the log row")

        monkeypatch.setattr(ModerationLog.objects, "create", boom)

    def test_approve_leaves_no_partial_state_if_log_creation_fails(
        self, queue_item, content, reviewer, monkeypatch
    ):
        # By the time the log row is created, the queue item and the
        # content object have already been saved inside the transaction.
        # Failing here proves those two saves are rolled back too.
        self._make_log_creation_fail(monkeypatch)

        with pytest.raises(RuntimeError):
            approve(queue_item, reviewer)

        _assert_untouched(queue_item, content)

    def test_reject_leaves_no_partial_state_if_log_creation_fails(
        self, queue_item, content, reviewer, monkeypatch
    ):
        self._make_log_creation_fail(monkeypatch)

        with pytest.raises(RuntimeError):
            reject(queue_item, reviewer, "Not allowed")

        _assert_untouched(queue_item, content)

    def test_missing_content_object_raises_and_changes_nothing(
        self, queue_item, content, reviewer
    ):
        # The generic FK has no database constraint, so the content row
        # can disappear while its queue row remains.
        content.delete()

        with pytest.raises(ValidationError):
            approve(queue_item, reviewer)

        assert _fresh_queue_status(queue_item) == ModerationQueue.Status.PENDING
        assert _log_count(queue_item) == 0


@pytest.mark.django_db
class TestModerationLogModel:
    def test_moderation_log_has_no_soft_delete_fields(self):
        field_names = {f.name for f in ModerationLog._meta.get_fields()}

        assert "is_deleted" not in field_names
        assert "deleted_at" not in field_names

    def test_moderation_log_has_no_all_objects_manager(self):
        assert not hasattr(ModerationLog, "all_objects")

    def test_queue_item_with_a_log_entry_cannot_be_deleted(
        self, queue_item, reviewer
    ):
        approve(queue_item, reviewer)

        with pytest.raises(ProtectedError):
            queue_item.delete()

        assert ModerationQueue.objects.filter(pk=queue_item.pk).exists()
        assert _log_count(queue_item) == 1

    def test_reviewer_with_a_log_entry_cannot_be_deleted(
        self, queue_item, reviewer
    ):
        approve(queue_item, reviewer)

        with pytest.raises(ProtectedError):
            reviewer.delete()

        assert _log_count(queue_item) == 1