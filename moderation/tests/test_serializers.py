"""
Tests for ModerationQueueSerializer (Part P-038).

Uses P-036's throwaway DummyContent model, which has no ``business``
attribute and does not override get_moderation_preview(), so the
generic paths are exercised with no content-type-specific code.
"""

import datetime
from types import SimpleNamespace

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from moderation.models import ModerationQueue
from moderation.serializers import ModerationQueueSerializer
from moderation.tests.testapp.models import DummyContent

pytestmark = pytest.mark.django_db


def _queue_item_for(content):
    return ModerationQueue.objects.get(
        content_type=ContentType.objects.get_for_model(content),
        object_id=content.pk,
    )


def test_serializes_expected_fields_without_submitter():
    content = DummyContent.objects.create(title="hello")

    data = ModerationQueueSerializer(_queue_item_for(content)).data

    assert set(data.keys()) == {
        "id",
        "content_type",
        "object_id",
        "status",
        "priority",
        "created_at",
        "age",
        "preview",
    }


def test_basic_values_for_a_new_item():
    content = DummyContent.objects.create(title="hello")
    queue_item = _queue_item_for(content)

    data = ModerationQueueSerializer(queue_item).data

    assert data["id"] == queue_item.pk
    assert data["content_type"] == "dummycontent"
    assert data["object_id"] == content.pk
    assert data["status"] == "pending"
    assert data["priority"] == "normal"
    assert isinstance(data["age"], int)
    assert data["age"] >= 0


def test_preview_uses_the_default_implementation():
    content = DummyContent.objects.create(title="hello")

    data = ModerationQueueSerializer(_queue_item_for(content)).data

    assert data["preview"] == {
        "preview_text": str(content),
        "preview_image_url": None,
    }


def test_preview_uses_a_subclass_override(monkeypatch):
    content = DummyContent.objects.create(title="hello")
    monkeypatch.setattr(
        DummyContent,
        "get_moderation_preview",
        lambda self: {
            "preview_text": "custom text",
            "preview_image_url": "https://example.com/thumb.jpg",
        },
    )

    data = ModerationQueueSerializer(_queue_item_for(content)).data

    assert data["preview"] == {
        "preview_text": "custom text",
        "preview_image_url": "https://example.com/thumb.jpg",
    }


def test_age_is_seconds_since_created_at():
    content = DummyContent.objects.create(title="hello")
    queue_item = _queue_item_for(content)
    ModerationQueue.objects.filter(pk=queue_item.pk).update(
        created_at=timezone.now() - datetime.timedelta(hours=1)
    )
    queue_item.refresh_from_db()

    data = ModerationQueueSerializer(queue_item).data

    assert 3600 <= data["age"] < 3660


def test_submitter_is_included_when_content_has_a_business(monkeypatch):
    content = DummyContent.objects.create(title="hello")
    monkeypatch.setattr(
        DummyContent,
        "business",
        SimpleNamespace(business_name="Acme Trading"),
        raising=False,
    )

    data = ModerationQueueSerializer(_queue_item_for(content)).data

    assert data["submitter"] == {"business_name": "Acme Trading"}


def test_submitter_is_omitted_when_business_is_none(monkeypatch):
    content = DummyContent.objects.create(title="hello")
    monkeypatch.setattr(DummyContent, "business", None, raising=False)

    data = ModerationQueueSerializer(_queue_item_for(content)).data

    assert "submitter" not in data


def test_preview_is_none_when_content_was_hard_deleted():
    content = DummyContent.objects.create(title="hello")
    queue_item = _queue_item_for(content)
    content.delete()

    data = ModerationQueueSerializer(queue_item).data

    assert data["preview"] is None
    assert "submitter" not in data
