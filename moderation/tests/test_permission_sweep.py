"""
P-096 (step 1) permission sweep for the moderation app.

test_api.py already proves: anonymous -> 401 envelope, customer -> 403
envelope + item still pending. This sweep adds what it does not:

  * a garbage Bearer token is 401 (not 500, not 200);
  * a Business-account user (e.g. the content's own owner) and a
    Django `is_staff` user with no capability are 403 - authorization
    is permission-based (Architecture Section 4), not is_staff-based;
  * every denied request leaves the queue item, the content and the
    ModerationLog table unchanged (follow-up query);
  * an unprivileged caller gets 403 even for an id that does not exist,
    so the endpoints cannot be used to enumerate queue ids.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from rest_framework.test import APIClient

from core.tests.sweep_helpers import assert_forbidden, assert_unauthenticated
from moderation.models import Moderatable, ModerationLog, ModerationQueue
from moderation.tests.testapp.models import DummyContent

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def pending_item():
    content = DummyContent.objects.create()
    item = ModerationQueue.objects.get(
        content_type=ContentType.objects.get_for_model(content),
        object_id=content.pk,
    )
    return content, item


def _requests(item_pk):
    return {
        "list": ("get", reverse("moderation-queue-list"), None),
        "approve": (
            "post",
            reverse("moderation-queue-approve", args=[item_pk]),
            None,
        ),
        "reject": (
            "post",
            reverse("moderation-queue-reject", args=[item_pk]),
            {"reason": "not allowed"},
        ),
    }


LABELS = ["list", "approve", "reject"]


def _send(client, method, url, payload):
    if method == "get":
        return client.get(url)
    return client.post(url, payload or {}, format="json")


def _assert_nothing_changed(content, item, logs_before):
    item.refresh_from_db()
    content.refresh_from_db()
    assert item.status == ModerationQueue.Status.PENDING
    assert content.status == Moderatable.Status.PENDING_REVIEW
    assert ModerationLog.objects.count() == logs_before


@pytest.mark.parametrize("label", LABELS)
def test_unauthenticated_gets_401_and_changes_nothing(client, pending_item, label):
    content, item = pending_item
    logs_before = ModerationLog.objects.count()
    method, url, payload = _requests(item.pk)[label]

    response = _send(client, method, url, payload)

    assert_unauthenticated(response)
    _assert_nothing_changed(content, item, logs_before)


@pytest.mark.parametrize("label", LABELS)
def test_garbage_bearer_token_gets_401_and_changes_nothing(client, pending_item, label):
    content, item = pending_item
    logs_before = ModerationLog.objects.count()
    client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    method, url, payload = _requests(item.pk)[label]

    response = _send(client, method, url, payload)

    assert_unauthenticated(response)
    _assert_nothing_changed(content, item, logs_before)


@pytest.mark.parametrize("actor", ["business_account", "staff_without_capability"])
@pytest.mark.parametrize("label", LABELS)
def test_user_without_capability_gets_403_and_changes_nothing(
    client, pending_item, label, actor
):
    content, item = pending_item
    logs_before = ModerationLog.objects.count()
    if actor == "business_account":
        user = User.objects.create_user(
            username="p096-biz-mod",
            password="pw12345",
            account_type=User.ACCOUNT_TYPE_BUSINESS,
        )
    else:
        user = User.objects.create_user(
            username="p096-staff-mod",
            password="pw12345",
            account_type=User.ACCOUNT_TYPE_CUSTOMER,
            is_staff=True,
        )
    client.force_authenticate(user)
    method, url, payload = _requests(item.pk)[label]

    response = _send(client, method, url, payload)

    assert_forbidden(response)
    _assert_nothing_changed(content, item, logs_before)


@pytest.mark.parametrize("label", ["approve", "reject"])
def test_unprivileged_caller_gets_403_not_404_for_unknown_id(client, label):
    user = User.objects.create_user(
        username="p096-enum",
        password="pw12345",
        account_type=User.ACCOUNT_TYPE_CUSTOMER,
    )
    client.force_authenticate(user)
    method, url, payload = _requests(999999)[label]

    response = _send(client, method, url, payload)

    assert_forbidden(response)
