"""
Integration tests for the four notification sources (Part P-079):
moderation decisions, follow, comment, and chat's offline fallback.

Each source is triggered for real; dispatch_delay (the autouse root
conftest fixture) captures what it enqueued. The "contract" tests then
feed the captured kwargs into the REAL dispatch_notification task body,
proving (a) the source's notification_type / deep_link fields are
accepted by create_notification's validation and (b) a disabled
preference category suppresses BOTH the in-app row and the push.

Lives in the notifications app so the moderation/social packages keep
their "never import each other's models" isolation intact.
"""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from businesses.models import BusinessProfile
from chat.models import Conversation, ConversationParticipant, Message
from chat.tasks import notify_offline_recipient
from content.models import Post, Reel
from moderation import services
from moderation.models import Moderatable, ModerationQueue
from moderation.tests.testapp.models import DummyContent
from notifications.models import Notification, NotificationPreference
from notifications.tasks import dispatch_notification

User = get_user_model()

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------- helpers


def _make_user(prefix, account_type="customer"):
    unique = uuid4().hex[:10]
    return User.objects.create_user(
        username=f"{prefix}-{unique}",
        email=f"{prefix}-{unique}@example.com",
        password="testpass123",
        account_type=account_type,
    )


def _make_business(name="Acme Trading"):
    return BusinessProfile.objects.create(
        user=_make_user("owner", "business"),
        business_name=name,
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )


def _publish(obj):
    fields = {"status": Moderatable.Status.PUBLISHED}
    if isinstance(obj, Reel):
        fields["processing_status"] = Reel.ProcessingStatus.READY
    type(obj).objects.filter(pk=obj.pk).update(**fields)
    obj.refresh_from_db()
    return obj


def _make_post(business=None, published=False):
    post = Post.objects.create(business=business or _make_business(), caption="Cap")
    return _publish(post) if published else post


def _make_reel(business=None, published=False):
    reel = Reel.objects.create(
        business=business or _make_business(),
        caption="Reel cap",
        video=SimpleUploadedFile(
            "raw.mp4",
            b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom",
            content_type="video/mp4",
        ),
    )
    return _publish(reel) if published else reel


def _queue_item_for(content):
    item, _ = ModerationQueue.objects.get_or_create(
        content_type=ContentType.objects.get_for_model(content),
        object_id=content.pk,
    )
    return item


def _run_task_with(call, **preferences):
    """Run the REAL dispatch_notification body with a captured call."""
    recipient = User.objects.get(pk=call.kwargs["recipient_id"])
    if preferences:
        NotificationPreference.objects.filter(user=recipient).update(**preferences)
    with patch("notifications.tasks.send_push_notification") as mock_push:
        dispatch_notification(**call.kwargs)
    return recipient, mock_push


def _follow(user, business):
    client = APIClient()
    client.force_authenticate(user=user)
    return client.post(reverse("social:business-follow", kwargs={"pk": business.pk}))


def _comment(user, content_type, obj, text="Nice one"):
    client = APIClient()
    client.force_authenticate(user=user)
    return client.post(
        reverse("comments:collection"),
        {"content_type": content_type, "object_id": obj.pk, "text": text},
        format="json",
    )


# ------------------------------------------------------------- moderation


class TestModerationSource:
    def test_approve_post_notifies_owner(self, dispatch_delay):
        post = _make_post()

        services.approve(_queue_item_for(post), _make_user("mod"))

        dispatch_delay.assert_called_once_with(
            recipient_id=post.business.user_id,
            notification_type="moderation_approved",
            title="Your post was approved",
            body="Your post is now published.",
            deep_link_type="post_detail",
            target_id=post.pk,
        )

    def test_reject_post_notifies_owner_with_reason(self, dispatch_delay):
        post = _make_post()

        services.reject(_queue_item_for(post), _make_user("mod"), "  Blurry image ")

        dispatch_delay.assert_called_once_with(
            recipient_id=post.business.user_id,
            notification_type="moderation_rejected",
            title="Your post was rejected",
            body="Reason: Blurry image",
            deep_link_type="post_detail",
            target_id=post.pk,
        )

    def test_approve_reel_uses_reel_detail(self, dispatch_delay):
        reel = _make_reel()

        services.approve(_queue_item_for(reel), _make_user("mod"))

        dispatch_delay.assert_called_once_with(
            recipient_id=reel.business.user_id,
            notification_type="moderation_approved",
            title="Your reel was approved",
            body="Your reel is now published.",
            deep_link_type="reel_detail",
            target_id=reel.pk,
        )

    def test_content_without_detail_screen_links_to_business_profile(
        self, dispatch_delay
    ):
        # Story has no detail screen in the P-078 contract.
        story_like = SimpleNamespace(
            _meta=SimpleNamespace(model_name="story"),
            pk=5,
            business=SimpleNamespace(pk=9, user_id=77),
        )

        services._notify_content_owner(story_like, approved=True)

        dispatch_delay.assert_called_once_with(
            recipient_id=77,
            notification_type="moderation_approved",
            title="Your story was approved",
            body="Your story is now published.",
            deep_link_type="business_profile",
            target_id=9,
        )

    def test_second_decision_does_not_notify_again(self, dispatch_delay):
        post = _make_post()
        item = _queue_item_for(post)
        reviewer = _make_user("mod")
        services.approve(item, reviewer)

        with pytest.raises(services.AlreadyDecidedError):
            services.approve(item, reviewer)

        assert dispatch_delay.call_count == 1

    def test_blank_reject_reason_does_not_notify(self, dispatch_delay):
        post = _make_post()

        with pytest.raises(ValidationError):
            services.reject(_queue_item_for(post), _make_user("mod"), "   ")

        dispatch_delay.assert_not_called()

    def test_content_without_business_sends_nothing(self, dispatch_delay):
        content = DummyContent.objects.create()
        item = _queue_item_for(content)

        services.approve(item, _make_user("mod"))

        dispatch_delay.assert_not_called()

    def test_broker_failure_does_not_break_the_decision(self, dispatch_delay):
        dispatch_delay.side_effect = RuntimeError("redis down")
        post = _make_post()
        item = _queue_item_for(post)

        log = services.approve(item, _make_user("mod"))

        post.refresh_from_db()
        item.refresh_from_db()
        assert log.pk is not None
        assert post.status == Moderatable.Status.PUBLISHED
        assert item.status == ModerationQueue.Status.APPROVED


# ----------------------------------------------------------------- follow


class TestFollowSource:
    def test_follow_notifies_business_owner(self, dispatch_delay):
        follower = _make_user("follower")
        business = _make_business()

        response = _follow(follower, business)

        assert response.status_code == 200
        dispatch_delay.assert_called_once_with(
            recipient_id=business.user_id,
            notification_type="new_follower",
            title="New follower",
            body="Someone started following your business.",
            deep_link_type="business_profile",
            target_id=business.pk,
        )

    def test_repeat_follow_does_not_notify_again(self, dispatch_delay):
        follower = _make_user("follower")
        business = _make_business()

        _follow(follower, business)
        _follow(follower, business)

        assert dispatch_delay.call_count == 1

    def test_following_your_own_business_does_not_notify(self, dispatch_delay):
        business = _make_business()

        response = _follow(business.user, business)

        assert response.status_code == 200
        dispatch_delay.assert_not_called()

    def test_unfollow_does_not_notify(self, dispatch_delay):
        follower = _make_user("follower")
        business = _make_business()
        _follow(follower, business)
        dispatch_delay.reset_mock()

        client = APIClient()
        client.force_authenticate(user=follower)
        response = client.delete(
            reverse("social:business-follow", kwargs={"pk": business.pk})
        )

        assert response.status_code == 200
        dispatch_delay.assert_not_called()

    def test_broker_failure_does_not_break_follow(self, dispatch_delay):
        dispatch_delay.side_effect = RuntimeError("redis down")
        follower = _make_user("follower")
        business = _make_business()

        response = _follow(follower, business)

        assert response.status_code == 200
        assert response.json() == {"following": True}


# ---------------------------------------------------------------- comment


class TestCommentSource:
    def test_comment_on_post_notifies_owner(self, dispatch_delay):
        post = _make_post(published=True)

        response = _comment(_make_user("commenter"), "post", post, text="Great post")

        assert response.status_code == 201
        dispatch_delay.assert_called_once_with(
            recipient_id=post.business.user_id,
            notification_type="comment_on_content",
            title="New comment",
            body="Great post",
            deep_link_type="post_detail",
            target_id=post.pk,
        )

    def test_comment_on_reel_uses_reel_detail(self, dispatch_delay):
        reel = _make_reel(published=True)

        response = _comment(_make_user("commenter"), "reel", reel)

        assert response.status_code == 201
        assert dispatch_delay.call_args.kwargs["deep_link_type"] == "reel_detail"
        assert dispatch_delay.call_args.kwargs["target_id"] == reel.pk

    def test_long_comment_body_is_truncated_to_100_chars(self, dispatch_delay):
        post = _make_post(published=True)

        _comment(_make_user("commenter"), "post", post, text="x" * 300)

        assert dispatch_delay.call_args.kwargs["body"] == "x" * 100

    def test_comment_on_own_content_does_not_notify(self, dispatch_delay):
        post = _make_post(published=True)

        response = _comment(post.business.user, "post", post)

        assert response.status_code == 201
        dispatch_delay.assert_not_called()

    def test_broker_failure_does_not_break_comment(self, dispatch_delay):
        dispatch_delay.side_effect = RuntimeError("redis down")
        post = _make_post(published=True)

        response = _comment(_make_user("commenter"), "post", post)

        assert response.status_code == 201


# --------------------------------------- source -> real task contract tests


class TestSourcesAgainstRealTask:
    def test_moderation_notification_is_accepted_and_suppressible(self, dispatch_delay):
        post = _make_post()
        services.approve(_queue_item_for(post), _make_user("mod"))
        call = dispatch_delay.call_args

        recipient, mock_push = _run_task_with(call)
        assert Notification.objects.filter(
            recipient=recipient,
            notification_type="moderation_approved",
            deep_link_type="post_detail",
            target_id=post.pk,
        ).exists()
        mock_push.assert_called_once()

        Notification.objects.all().delete()
        _, mock_push = _run_task_with(call, moderation_notifications_enabled=False)
        assert Notification.objects.filter(recipient=recipient).count() == 0
        mock_push.assert_not_called()

    def test_follow_notification_is_accepted_and_suppressible(self, dispatch_delay):
        business = _make_business()
        _follow(_make_user("follower"), business)
        call = dispatch_delay.call_args

        recipient, mock_push = _run_task_with(call)
        assert Notification.objects.filter(
            recipient=recipient, notification_type="new_follower"
        ).exists()
        mock_push.assert_called_once()

        Notification.objects.all().delete()
        _, mock_push = _run_task_with(call, social_notifications_enabled=False)
        assert Notification.objects.filter(recipient=recipient).count() == 0
        mock_push.assert_not_called()

    def test_comment_notification_is_accepted_and_suppressible(self, dispatch_delay):
        post = _make_post(published=True)
        _comment(_make_user("commenter"), "post", post)
        call = dispatch_delay.call_args

        recipient, mock_push = _run_task_with(call)
        assert Notification.objects.filter(
            recipient=recipient, notification_type="comment_on_content"
        ).exists()
        mock_push.assert_called_once()

        Notification.objects.all().delete()
        _, mock_push = _run_task_with(call, social_notifications_enabled=False)
        assert Notification.objects.filter(recipient=recipient).count() == 0
        mock_push.assert_not_called()

    def test_chat_offline_notification_is_accepted_and_suppressible(
        self, dispatch_delay
    ):
        sender = _make_user("chat_sender")
        recipient_user = _make_user("chat_recipient")
        conversation = Conversation.objects.create()
        ConversationParticipant.objects.create(conversation=conversation, user=sender)
        ConversationParticipant.objects.create(
            conversation=conversation, user=recipient_user
        )
        message = Message.objects.create(
            conversation=conversation, sender=sender, text="hi there"
        )
        notify_offline_recipient(message.id)
        call = dispatch_delay.call_args

        recipient, mock_push = _run_task_with(call)
        assert Notification.objects.filter(
            recipient=recipient,
            notification_type="chat_message",
            deep_link_type="chat_thread",
            target_id=conversation.pk,
        ).exists()
        mock_push.assert_called_once()

        Notification.objects.all().delete()
        _, mock_push = _run_task_with(call, chat_notifications_enabled=False)
        assert Notification.objects.filter(recipient=recipient).count() == 0
        mock_push.assert_not_called()
