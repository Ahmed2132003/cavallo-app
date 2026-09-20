"""
Tests for the default ``Moderatable.get_moderation_preview()`` (Part P-038).

Uses P-036's throwaway DummyContent model, which does NOT override the
method, so these tests exercise the mixin's default implementation.
"""

import pytest

from moderation.tests.testapp.models import DummyContent

pytestmark = pytest.mark.django_db


def test_default_preview_has_exactly_the_contract_keys():
    item = DummyContent.objects.create(title="hello")

    preview = item.get_moderation_preview()

    assert set(preview.keys()) == {"preview_text", "preview_image_url"}


def test_default_preview_text_is_str_of_the_object():
    item = DummyContent.objects.create(title="hello")

    preview = item.get_moderation_preview()

    assert preview["preview_text"] == str(item)


def test_default_preview_image_url_is_none():
    item = DummyContent.objects.create(title="hello")

    assert item.get_moderation_preview()["preview_image_url"] is None


def test_default_preview_text_is_truncated_to_200_characters(monkeypatch):
    item = DummyContent.objects.create(title="hello")
    monkeypatch.setattr(DummyContent, "__str__", lambda self: "x" * 500)

    preview = item.get_moderation_preview()

    assert preview["preview_text"] == "x" * 200


def test_default_preview_returns_a_fresh_dict_each_call():
    item = DummyContent.objects.create(title="hello")

    first = item.get_moderation_preview()
    first["preview_text"] = "mutated"
    second = item.get_moderation_preview()

    assert second["preview_text"] != "mutated"
