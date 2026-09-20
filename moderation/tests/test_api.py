"""
API tests for the moderator queue endpoints (Part P-038).

Uses P-036's throwaway DummyContent model (no override of
get_moderation_preview(), no ``business`` attribute), so everything here
runs without any Post/Reel/Story code. The Moderator/Admin Groups come
from the accounts.0003 data migration (P-019).
"""

from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from rest_framework.test import APIClient

from moderation import services
from moderation.models import Moderatable, ModerationLog, ModerationQueue
from moderation.tests.testapp.models import DummyContent

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


def _make_user(username, group=None):
    user = User.objects.create_user(
        username=username,
        password="pw12345",
        account_type=User.ACCOUNT_TYPE_CUSTOMER,
    )
    if group:
        user.groups.add(Group.objects.get(name=group))
    return user


@pytest.fixture
def moderator():
    return _make_user("moderator1", "Moderator")


@pytest.fixture
def customer():
    return _make_user("customer1")


def _make_item(**content_kwargs):
    content = DummyContent.objects.create(**content_kwargs)
    queue_item = ModerationQueue.objects.get(
        content_type=ContentType.objects.get_for_model(content),
        object_id=content.pk,
    )
    return content, queue_item


def _list_url():
    return reverse("moderation-queue-list")


def _approve_url(pk):
    return reverse("moderation-queue-approve", args=[pk])


def _reject_url(pk):
    return reverse("moderation-queue-reject", args=[pk])


class TestPermissions:
    def test_unauthenticated_gets_401_on_all_three(self, client):
        _, item = _make_item()

        responses = [
            client.get(_list_url()),
            client.post(_approve_url(item.pk)),
            client.post(_reject_url(item.pk), {"reason": "x"}, format="json"),
        ]

        for response in responses:
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "AUTHENTICATION_FAILED"

    def test_customer_gets_403_on_all_three(self, client, customer):
        content, item = _make_item()
        client.force_authenticate(customer)

        responses = [
            client.get(_list_url()),
            client.post(_approve_url(item.pk)),
            client.post(_reject_url(item.pk), {"reason": "x"}, format="json"),
        ]

        for response in responses:
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "PERMISSION_DENIED"
        item.refresh_from_db()
        content.refresh_from_db()
        assert item.status == ModerationQueue.Status.PENDING
        assert content.status == Moderatable.Status.PENDING_REVIEW

    def test_admin_group_user_is_allowed(self, client):
        client.force_authenticate(_make_user("admin1", "Admin"))

        assert client.get(_list_url()).status_code == 200


class TestList:
    def test_moderator_lists_pending_items_with_generic_preview(
        self, client, moderator
    ):
        content, item = _make_item(title="hello")
        client.force_authenticate(moderator)

        response = client.get(_list_url())

        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 1
        entry = results[0]
        assert entry["id"] == item.pk
        assert entry["content_type"] == "dummycontent"
        assert entry["object_id"] == content.pk
        assert entry["status"] == "pending"
        assert entry["priority"] == "normal"
        assert entry["preview"] == {
            "preview_text": str(content),
            "preview_image_url": None,
        }
        assert "submitter" not in entry

    def test_list_excludes_decided_items(self, client, moderator):
        _, pending = _make_item()
        _, decided = _make_item()
        services.approve(decided, moderator)
        client.force_authenticate(moderator)

        results = client.get(_list_url()).json()["results"]

        assert [entry["id"] for entry in results] == [pending.pk]

    def test_list_is_cursor_paginated(self, client, moderator):
        for _ in range(25):
            _make_item()
        client.force_authenticate(moderator)

        first = client.get(_list_url()).json()
        assert len(first["results"]) == 20
        assert first["next"] is not None

        parsed = urlparse(first["next"])
        second = client.get(f"{parsed.path}?{parsed.query}").json()
        assert len(second["results"]) == 5
        assert second["next"] is None

        first_ids = {entry["id"] for entry in first["results"]}
        second_ids = {entry["id"] for entry in second["results"]}
        assert first_ids.isdisjoint(second_ids)

    def test_priority_filter_narrows_the_list(self, client, moderator):
        _, normal_item = _make_item()
        _, fast_item = _make_item()
        ModerationQueue.objects.filter(pk=fast_item.pk).update(
            priority=ModerationQueue.Priority.FAST_PATH
        )
        client.force_authenticate(moderator)

        filtered = client.get(_list_url(), {"priority": "fast_path"}).json()
        everything = client.get(_list_url()).json()

        assert [e["id"] for e in filtered["results"]] == [fast_item.pk]
        assert {e["id"] for e in everything["results"]} == {
            normal_item.pk,
            fast_item.pk,
        }

    def test_invalid_priority_returns_400(self, client, moderator):
        client.force_authenticate(moderator)

        response = client.get(_list_url(), {"priority": "urgent"})

        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert "priority" in error["fields"]


class TestApprove:
    def test_approve_publishes_and_returns_the_updated_item(self, client, moderator):
        content, item = _make_item()
        client.force_authenticate(moderator)

        response = client.post(_approve_url(item.pk))

        assert response.status_code == 200
        assert response.json()["id"] == item.pk
        assert response.json()["status"] == "approved"
        item.refresh_from_db()
        content.refresh_from_db()
        assert item.status == ModerationQueue.Status.APPROVED
        assert content.status == Moderatable.Status.PUBLISHED
        log = ModerationLog.objects.get(queue_item=item)
        assert log.action == ModerationLog.Action.APPROVED
        assert log.reviewer == moderator

    def test_approve_delegates_to_the_service(self, client, moderator, monkeypatch):
        _, item = _make_item()
        calls = []
        monkeypatch.setattr(
            services,
            "approve",
            lambda queue_item, reviewer: calls.append((queue_item.pk, reviewer.pk)),
        )
        client.force_authenticate(moderator)

        response = client.post(_approve_url(item.pk))

        assert response.status_code == 200
        assert calls == [(item.pk, moderator.pk)]

    def test_approving_twice_returns_409_and_keeps_one_log(self, client, moderator):
        _, item = _make_item()
        client.force_authenticate(moderator)
        assert client.post(_approve_url(item.pk)).status_code == 200

        response = client.post(_approve_url(item.pk))

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"
        assert ModerationLog.objects.filter(queue_item=item).count() == 1

    def test_unknown_id_returns_404(self, client, moderator):
        client.force_authenticate(moderator)

        response = client.post(_approve_url(999999))

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    def test_hard_deleted_content_returns_400_not_500(self, client, moderator):
        content, item = _make_item()
        content.delete()
        client.force_authenticate(moderator)

        response = client.post(_approve_url(item.pk))

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        item.refresh_from_db()
        assert item.status == ModerationQueue.Status.PENDING
        assert not ModerationLog.objects.filter(queue_item=item).exists()


class TestReject:
    def test_reject_with_reason_returns_the_updated_item(self, client, moderator):
        content, item = _make_item()
        client.force_authenticate(moderator)

        response = client.post(
            _reject_url(item.pk), {"reason": "  Blurry image  "}, format="json"
        )

        assert response.status_code == 200
        assert response.json()["id"] == item.pk
        assert response.json()["status"] == "rejected"
        item.refresh_from_db()
        content.refresh_from_db()
        assert item.status == ModerationQueue.Status.REJECTED
        assert content.status == Moderatable.Status.REJECTED
        log = ModerationLog.objects.get(queue_item=item)
        assert log.action == ModerationLog.Action.REJECTED
        assert log.reason == "Blurry image"
        assert log.reviewer == moderator

    def test_reject_delegates_to_the_service(self, client, moderator, monkeypatch):
        _, item = _make_item()
        calls = []
        monkeypatch.setattr(
            services,
            "reject",
            lambda queue_item, reviewer, reason: calls.append(
                (queue_item.pk, reviewer.pk, reason)
            ),
        )
        client.force_authenticate(moderator)

        response = client.post(
            _reject_url(item.pk), {"reason": "Too blurry"}, format="json"
        )

        assert response.status_code == 200
        assert calls == [(item.pk, moderator.pk, "Too blurry")]

    @pytest.mark.parametrize(
        "body",
        [{}, {"reason": ""}, {"reason": "   "}, {"reason": None}],
        ids=["missing", "empty", "whitespace", "null"],
    )
    def test_reject_without_a_reason_returns_400_envelope(
        self, client, moderator, body
    ):
        content, item = _make_item()
        client.force_authenticate(moderator)

        response = client.post(_reject_url(item.pk), body, format="json")

        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert isinstance(error["message"], str) and error["message"]
        assert "reason" in error["fields"]
        item.refresh_from_db()
        content.refresh_from_db()
        assert item.status == ModerationQueue.Status.PENDING
        assert content.status == Moderatable.Status.PENDING_REVIEW
        assert not ModerationLog.objects.filter(queue_item=item).exists()

    def test_rejecting_an_already_approved_item_returns_409(self, client, moderator):
        _, item = _make_item()
        client.force_authenticate(moderator)
        assert client.post(_approve_url(item.pk)).status_code == 200

        response = client.post(
            _reject_url(item.pk), {"reason": "Changed my mind"}, format="json"
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"
        assert ModerationLog.objects.filter(queue_item=item).count() == 1

    def test_unknown_id_returns_404(self, client, moderator):
        client.force_authenticate(moderator)

        response = client.post(
            _reject_url(999999), {"reason": "Not needed"}, format="json"
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
