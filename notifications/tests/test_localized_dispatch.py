"""
Tests for server-side notification localization (Part P-112).

dispatch_notification renders title/body from the gettext catalogs in
the RECIPIENT's preferred_language; the push for each device uses that
device's registered locale. User-generated text is never translated.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from devices.models import DeviceToken
from notifications.messages import render_notification
from notifications.models import Notification
from notifications.tasks import dispatch_notification

User = get_user_model()

pytestmark = pytest.mark.django_db


def _make_user(username, language=None):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        account_type="customer",
    )
    if language is not None:
        User.objects.filter(pk=user.pk).update(preferred_language=language)
        user.refresh_from_db()
    return user


def _dispatch(user, **overrides):
    kwargs = dict(
        recipient_id=user.id,
        notification_type="new_follower",
        title="New follower",
        body="Someone started following your business.",
        deep_link_type="business_profile",
        target_id=3,
        params={},
    )
    kwargs.update(overrides)
    with patch("notifications.tasks.send_push_notification") as mock_push:
        dispatch_notification(**kwargs)
    return mock_push


class TestRenderNotification:
    def test_follower_in_arabic_and_english(self):
        assert render_notification("new_follower", {}, "en") == (
            "New follower",
            "Someone started following your business.",
        )
        title, body = render_notification("new_follower", {}, "ar")
        assert title == "متابع جديد"
        assert body == "بدأ شخص ما بمتابعة نشاطك التجاري."

    @pytest.mark.parametrize("item", ["post", "reel", "story", "unknown"])
    @pytest.mark.parametrize("kind", ["moderation_approved", "moderation_rejected"])
    def test_every_moderation_item_has_distinct_arabic_text(self, item, kind):
        english = render_notification(kind, {"item": item, "reason": "r"}, "en")
        arabic = render_notification(kind, {"item": item, "reason": "r"}, "ar")
        assert arabic is not None and english is not None
        assert arabic[0] != english[0]
        assert arabic[1] != english[1]

    def test_rejection_reason_is_inserted_untranslated(self):
        _title, body = render_notification(
            "moderation_rejected", {"item": "post", "reason": "Blurry image"}, "ar"
        )
        assert body == "السبب: Blurry image"

    def test_user_text_is_never_translated(self):
        # Even text identical to a catalog msgid stays exactly as typed.
        for text in ("New message", "New follower"):
            assert render_notification("comment_on_content", {"text": text}, "ar") == (
                "تعليق جديد",
                text,
            )
            assert render_notification("chat_message", {"text": text}, "ar") == (
                "رسالة جديدة",
                text,
            )

    @pytest.mark.parametrize(
        "params, arabic_body",
        [
            ({"media": "image"}, "أرسل صورة"),
            ({"media": "video"}, "أرسل مقطع فيديو"),
            ({"shared": "post"}, "شارك منشورًا"),
            ({"shared": "reel"}, "شارك ريل"),
            ({"shared": "product"}, "شارك منتجًا"),
        ],
    )
    def test_chat_media_and_shared_bodies(self, params, arabic_body):
        assert render_notification("chat_message", params, "ar")[1] == arabic_body

    def test_every_notification_type_is_renderable(self):
        samples = {
            "system_announcement": {"title": "T", "body": "B"},
            "comment_on_content": {"text": "x"},
            "chat_message": {"text": "x"},
        }
        for value in Notification.NotificationType.values:
            params = samples.get(value, {"item": "post"})
            for language in ("ar", "en"):
                assert render_notification(value, params, language) is not None

    def test_insufficient_params_return_none(self):
        assert render_notification("comment_on_content", {}, "ar") is None
        assert render_notification("chat_message", {"shared": ""}, "ar") is None
        assert render_notification("no_such_type", {}, "ar") is None

    def test_unsupported_language_falls_back_to_english(self):
        assert render_notification("new_follower", {}, "fr")[0] == "New follower"


class TestDispatchLocalization:
    def test_arabic_user_gets_arabic_row(self):
        user = _make_user("ar_user", "ar")

        _dispatch(user)

        row = Notification.objects.get(recipient=user)
        assert row.title == "متابع جديد"
        assert row.body == "بدأ شخص ما بمتابعة نشاطك التجاري."
        assert row.params == {}

    def test_english_user_gets_english_row(self):
        user = _make_user("en_user", "en")

        _dispatch(user)

        row = Notification.objects.get(recipient=user)
        assert row.title == "New follower"

    def test_new_user_defaults_to_arabic(self):
        user = _make_user("default_user")

        _dispatch(user)

        assert Notification.objects.get(recipient=user).title == "متابع جديد"

    def test_params_are_stored_with_the_row(self):
        user = _make_user("params_user", "en")

        _dispatch(
            user,
            notification_type="moderation_rejected",
            title="Your post was rejected",
            body="Reason: x",
            params={"item": "post", "reason": "x"},
        )

        row = Notification.objects.get(recipient=user)
        assert row.params == {"item": "post", "reason": "x"}
        assert row.title == "Your post was rejected"
        assert row.body == "Reason: x"

    def test_without_params_supplied_text_is_stored_as_is(self):
        user = _make_user("legacy_user", "ar")

        _dispatch(user, title="Custom", body="Literal body", params=None)

        row = Notification.objects.get(recipient=user)
        assert (row.title, row.body) == ("Custom", "Literal body")
        assert row.params == {}

    def test_unrenderable_params_keep_supplied_text(self):
        user = _make_user("fallback_user", "ar")

        _dispatch(
            user,
            notification_type="comment_on_content",
            title="New comment",
            body="hi",
            params={},
        )

        row = Notification.objects.get(recipient=user)
        assert (row.title, row.body) == ("New comment", "hi")

    def test_push_gets_recipient_language_and_all_languages_map(self):
        user = _make_user("push_user", "ar")

        mock_push = _dispatch(user)

        kwargs = mock_push.call_args.kwargs
        assert kwargs["title"] == "متابع جديد"
        assert kwargs["localized"]["en"][0] == "New follower"
        assert kwargs["localized"]["ar"][0] == "متابع جديد"

    def test_without_params_push_call_is_unchanged(self):
        user = _make_user("plain_push_user", "ar")

        mock_push = _dispatch(user, params=None)

        assert "localized" not in mock_push.call_args.kwargs


class TestPerDevicePushLanguage:
    def _sent_titles(self, user):
        from notifications import services

        titles = {}

        def fake_send(message, app=None):
            titles[message.token] = message.notification.title

        localized = {
            "ar": ("متابع جديد", "ب"),
            "en": ("New follower", "b"),
        }
        with patch.object(services, "_get_firebase_app", return_value=object()):
            with patch.object(services.messaging, "send", side_effect=fake_send):
                services.send_push_notification(
                    user.id, "متابع جديد", "ب", {"type": "x"}, localized=localized
                )
        return titles

    def test_each_device_receives_its_own_locale(self):
        user = _make_user("multi_device", "ar")
        DeviceToken.objects.create(user=user, token="t-en", platform="ios", locale="en")
        DeviceToken.objects.create(
            user=user, token="t-ar", platform="android", locale="ar"
        )
        DeviceToken.objects.create(
            user=user, token="t-blank", platform="android", locale=""
        )

        titles = self._sent_titles(user)

        assert titles == {
            "t-en": "New follower",
            "t-ar": "متابع جديد",
            "t-blank": "متابع جديد",  # unknown locale -> account language
        }
