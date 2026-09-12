"""
Tests for core.models (TimestampedModel, SoftDeleteModel, SoftDeleteManager).

Exercises the mixins against core.tests.testapp.Widget — a real table in
the test database (see config/settings/test.py) — rather than mocking
manager/queryset behavior, since the acceptance criteria calls for
actual soft-delete/hard-delete round trips against the database.
"""

import pytest

from core.tests.testapp.models import Widget


@pytest.mark.django_db
class TestTimestampedModel:
    def test_created_at_and_updated_at_are_set_on_create(self):
        widget = Widget.objects.create(name="alpha")

        assert widget.created_at is not None
        assert widget.updated_at is not None

    def test_updated_at_changes_on_save_but_created_at_does_not(self):
        widget = Widget.objects.create(name="alpha")
        original_created_at = widget.created_at
        original_updated_at = widget.updated_at

        widget.name = "alpha-renamed"
        widget.save()
        widget.refresh_from_db()

        assert widget.created_at == original_created_at
        assert widget.updated_at > original_updated_at


@pytest.mark.django_db
class TestSoftDeleteModel:
    def test_default_manager_excludes_soft_deleted_rows(self):
        kept = Widget.objects.create(name="kept")
        deleted = Widget.objects.create(name="deleted")

        deleted.delete()

        assert list(Widget.objects.all()) == [kept]
        assert Widget.objects.filter(pk=deleted.pk).count() == 0

    def test_all_objects_includes_soft_deleted_rows(self):
        kept = Widget.objects.create(name="kept")
        deleted = Widget.objects.create(name="deleted")

        deleted.delete()

        assert set(Widget.all_objects.all()) == {kept, deleted}

    def test_delete_sets_is_deleted_and_deleted_at_without_removing_row(self):
        widget = Widget.objects.create(name="alpha")

        widget.delete()
        widget.refresh_from_db()

        assert widget.is_deleted is True
        assert widget.deleted_at is not None
        # Row still exists in the database — this was a soft delete.
        assert Widget.all_objects.filter(pk=widget.pk).exists()

    def test_delete_also_bumps_updated_at(self):
        widget = Widget.objects.create(name="alpha")
        original_updated_at = widget.updated_at

        widget.delete()
        widget.refresh_from_db()

        assert widget.updated_at > original_updated_at

    def test_hard_delete_actually_removes_the_row(self):
        widget = Widget.objects.create(name="alpha")
        pk = widget.pk

        widget.hard_delete()

        assert not Widget.all_objects.filter(pk=pk).exists()

    def test_hard_delete_is_a_separate_method_from_delete(self):
        soft = Widget.objects.create(name="soft")
        hard = Widget.objects.create(name="hard")

        soft.delete()
        hard.hard_delete()

        # Soft-deleted row still physically exists; hard-deleted one does not.
        assert Widget.all_objects.filter(pk=soft.pk).exists()
        assert not Widget.all_objects.filter(pk=hard.pk).exists()
