"""
Service tests for Part P-055 — auto-hide threshold.

The real trigger (Report submission, P-057) doesn't exist yet, so
these tests drive the seam directly: they persist a `reports_count`
straight into the database (WITHOUT touching the in-memory instance,
exactly like P-057's future F()-based increment will) and then call
`check_and_hide_if_threshold_exceeded`.
"""

import ast
import inspect

from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from businesses.models import BusinessProfile
from content.models import Post
from moderation.models import ModerationQueue
from social import models as social_models
from social import serializers as social_serializers
from social import services
from social import views as social_views
from social.models import Comment
from social.services import (
    COMMENT_AUTO_HIDE_THRESHOLD,
    check_and_hide_if_threshold_exceeded,
)

User = get_user_model()

pytestmark = pytest.mark.django_db


def _make_comment():
    owner = User.objects.create_user(
        username=f"owner-{uuid4().hex[:12]}",
        password="pass12345",
        account_type="business",
    )
    business = BusinessProfile.objects.create(
        user=owner,
        business_name="Test Business",
        business_type="trader",
        country="Egypt",
        city="Cairo",
    )
    post = Post.objects.create(business=business, caption="hi")
    commenter = User.objects.create_user(
        username=f"commenter-{uuid4().hex[:12]}",
        password="pass12345",
        account_type="customer",
    )
    return Comment.objects.create(
        user=commenter,
        content_type=ContentType.objects.get_for_model(Post),
        object_id=post.pk,
        text="nice",
    )


def _persist_reports(comment, count):
    # Deliberately bypasses the instance: mimics P-057's F() increment.
    Comment.objects.filter(pk=comment.pk).update(reports_count=count)


class TestThresholdConstant:
    def test_threshold_is_a_positive_integer(self):
        assert isinstance(COMMENT_AUTO_HIDE_THRESHOLD, int)
        assert COMMENT_AUTO_HIDE_THRESHOLD >= 1


class TestCheckAndHideIfThresholdExceeded:
    def test_zero_reports_does_not_hide(self):
        comment = _make_comment()

        assert check_and_hide_if_threshold_exceeded(comment) is False
        comment.refresh_from_db()
        assert comment.is_hidden is False

    def test_just_below_threshold_does_not_hide(self):
        comment = _make_comment()
        _persist_reports(comment, COMMENT_AUTO_HIDE_THRESHOLD - 1)

        assert check_and_hide_if_threshold_exceeded(comment) is False
        assert comment.is_hidden is False
        comment.refresh_from_db()
        assert comment.is_hidden is False

    def test_at_threshold_hides_and_returns_true(self):
        comment = _make_comment()
        _persist_reports(comment, COMMENT_AUTO_HIDE_THRESHOLD)

        assert check_and_hide_if_threshold_exceeded(comment) is True
        assert comment.is_hidden is True
        comment.refresh_from_db()
        assert comment.is_hidden is True

    def test_above_threshold_hides_and_returns_true(self):
        comment = _make_comment()
        _persist_reports(comment, COMMENT_AUTO_HIDE_THRESHOLD + 10)

        assert check_and_hide_if_threshold_exceeded(comment) is True
        comment.refresh_from_db()
        assert comment.is_hidden is True

    def test_second_call_returns_false_because_already_hidden(self):
        comment = _make_comment()
        _persist_reports(comment, COMMENT_AUTO_HIDE_THRESHOLD)

        assert check_and_hide_if_threshold_exceeded(comment) is True
        assert check_and_hide_if_threshold_exceeded(comment) is False
        comment.refresh_from_db()
        assert comment.is_hidden is True

    def test_threshold_is_tunable_via_module_constant(self, monkeypatch):
        monkeypatch.setattr(services, "COMMENT_AUTO_HIDE_THRESHOLD", 2)
        comment = _make_comment()

        _persist_reports(comment, 1)
        assert check_and_hide_if_threshold_exceeded(comment) is False

        _persist_reports(comment, 2)
        assert check_and_hide_if_threshold_exceeded(comment) is True

    def test_hiding_creates_no_moderation_queue_row(self):
        comment = _make_comment()
        _persist_reports(comment, COMMENT_AUTO_HIDE_THRESHOLD)
        before = ModerationQueue.objects.count()

        check_and_hide_if_threshold_exceeded(comment)

        assert ModerationQueue.objects.count() == before


def _imported_module_names(module):
    tree = ast.parse(inspect.getsource(module))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestCommentFlowIsolatedFromModeration:
    """
    Structural negative test: the modules that make up the Comment flow
    must never import the moderation app (Comment is the deliberate,
    confirmed exception to the Moderatable pattern). The behavioral
    proof (zero ModerationQueue rows) lives in test_models/test_api.
    """

    @pytest.mark.parametrize(
        "module",
        [social_models, social_views, social_serializers, services],
        ids=["models", "views", "serializers", "services"],
    )
    def test_module_never_imports_the_moderation_app(self, module):
        offending = {
            name
            for name in _imported_module_names(module)
            if name == "moderation" or name.startswith("moderation.")
        }
        assert offending == set()
