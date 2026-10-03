"""
Part P-095 (Phase 17) - deep-link cross-navigation sweep, backend half.

For every notification source that P-094 did not already cover, trigger the
REAL event, push the kwargs the source enqueued through the REAL
dispatch_notification body (what a Celery worker would do), then check:

  1. the stored Notification row (read back through the real
     GET /api/v1/notifications/ endpoint) carries the right
     notification_type / deep_link_type / target_id, and
  2. the screen that deep link opens would load THAT exact item: the same
     GET the Flutter detail screens issue (/api/v1/posts/<id>/,
     /api/v1/reels/<id>/) returns the specific Post/Reel the event was about.

P-094 already covered new_follower and chat_message.

Known gaps are pinned, not hidden:
  * G-1: nothing ever sends new_like / new_share / new_rating /
    system_announcement. The new_like test is xfail(strict=True) so it turns
    into a hard failure (XPASS) the day a source is added.
  * D-1 (rejected / not-yet-ready content opens a "not found" screen in
    Flutter) is a mobile-side defect; it is pinned by the Flutter test, not
    here. This file only proves the backend serves the rejection reason.
"""

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from core.tests.test_integration_phase17 import _results
from moderation import services
from notifications.models import Notification
from notifications.tasks import dispatch_notification
from notifications.tests.test_sources import (
    _comment,
    _make_post,
    _make_reel,
    _make_user,
    _queue_item_for,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------- helpers


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _deliver(dispatch_delay):
    """Run the real dispatch_notification body with what the source enqueued."""
    assert dispatch_delay.call_count == 1, dispatch_delay.call_args_list
    kwargs = dispatch_delay.call_args.kwargs
    with patch("notifications.tasks.send_push_notification"):
        dispatch_notification(**kwargs)
    dispatch_delay.reset_mock()
    return kwargs


def _only_notification(user):
    response = _client(user).get("/api/v1/notifications/")
    assert response.status_code == 200, response.content
    rows = _results(response)
    assert len(rows) == 1, rows
    return rows[0]


def _assert_row(row, notification_type, deep_link_type, target_id):
    assert row["notification_type"] == notification_type
    assert row["deep_link_type"] == deep_link_type
    assert row["target_id"] == target_id
    assert row["is_read"] is False


def _open_detail(user, kind, target_id):
    """The same GET the Flutter detail screen issues for /post/:id, /reel/:id."""
    response = _client(user).get(f"/api/v1/{kind}s/{target_id}/")
    assert response.status_code == 200, response.content
    return response.json()


# ------------------------------------------------------------ moderation


class TestModerationDeepLinks:
    def test_approved_post_links_to_that_post(self, dispatch_delay):
        post = _make_post()
        owner = post.business.user

        services.approve(_queue_item_for(post), _make_user("mod"))
        _deliver(dispatch_delay)

        row = _only_notification(owner)
        _assert_row(row, "moderation_approved", "post_detail", post.pk)
        detail = _open_detail(owner, "post", row["target_id"])
        assert detail["id"] == post.pk
        assert detail["status"] == "published"

    def test_approved_reel_links_to_that_reel(self, dispatch_delay):
        reel = _make_reel()
        owner = reel.business.user

        services.approve(_queue_item_for(reel), _make_user("mod"))
        _deliver(dispatch_delay)

        row = _only_notification(owner)
        _assert_row(row, "moderation_approved", "reel_detail", reel.pk)
        detail = _open_detail(owner, "reel", row["target_id"])
        assert detail["id"] == reel.pk
        assert detail["status"] == "published"

    def test_rejected_post_links_to_that_post_and_serves_the_reason(
        self, dispatch_delay
    ):
        post = _make_post()
        owner = post.business.user

        services.reject(_queue_item_for(post), _make_user("mod"), "Blurry image")
        _deliver(dispatch_delay)

        row = _only_notification(owner)
        _assert_row(row, "moderation_rejected", "post_detail", post.pk)
        assert row["body"] == "Reason: Blurry image"
        detail = _open_detail(owner, "post", row["target_id"])
        assert detail["id"] == post.pk
        assert detail["status"] == "rejected"
        assert detail["rejection_reason"] == "Blurry image"


# --------------------------------------------------------------- comments


class TestCommentDeepLinks:
    def test_comment_on_post_links_to_that_post(self, dispatch_delay):
        post = _make_post(published=True)
        owner = post.business.user
        customer = _make_user("cust")

        response = _comment(customer, "post", post, "Great product")
        assert response.status_code == 201, response.content
        _deliver(dispatch_delay)

        row = _only_notification(owner)
        _assert_row(row, "comment_on_content", "post_detail", post.pk)
        assert _open_detail(owner, "post", row["target_id"])["id"] == post.pk
        assert _results(_client(customer).get("/api/v1/notifications/")) == []

    def test_comment_on_reel_links_to_that_reel(self, dispatch_delay):
        reel = _make_reel(published=True)
        owner = reel.business.user
        customer = _make_user("cust")

        response = _comment(customer, "reel", reel, "Love it")
        assert response.status_code == 201, response.content
        _deliver(dispatch_delay)

        row = _only_notification(owner)
        _assert_row(row, "comment_on_content", "reel_detail", reel.pk)
        assert _open_detail(owner, "reel", row["target_id"])["id"] == reel.pk


# ------------------------------------------------------------------- likes


@pytest.mark.xfail(
    strict=True,
    reason=(
        "G-1: LikeToggleView never enqueues a notification, so new_like has no "
        "source. Adding one is new scope (out of scope for P-095)."
    ),
)
def test_like_on_reel_notifies_owner_and_links_to_that_reel(dispatch_delay):
    reel = _make_reel(published=True)
    owner = reel.business.user
    customer = _make_user("cust")

    response = _client(customer).post(
        "/api/v1/likes/",
        {"content_type": "reel", "object_id": reel.pk},
        format="json",
    )
    assert response.status_code == 200, response.content

    _deliver(dispatch_delay)  # fails today: nothing was enqueued
    row = _only_notification(owner)
    _assert_row(row, "new_like", "reel_detail", reel.pk)


# ------------------------------------------------------------------ guard

COVERED_BY_THIS_SWEEP = {
    "moderation_approved",
    "moderation_rejected",
    "comment_on_content",
}
COVERED_BY_P094 = {"new_follower", "chat_message"}
DOCUMENTED_GAPS_G1 = {  # defined but no source ever sends them
    "new_like",
    "new_share",
    "new_rating",
    "system_announcement",
}


def test_every_notification_type_is_classified():
    """A new NotificationType must be covered or documented as a gap."""
    classified = COVERED_BY_THIS_SWEEP | COVERED_BY_P094 | DOCUMENTED_GAPS_G1
    assert set(Notification.NotificationType.values) == classified
