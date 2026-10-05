"""
Tests for the real send_push_notification() body (Part P-081).

Firebase is NEVER contacted: every test patches
``notifications.services.messaging.send`` and
``notifications.services._get_firebase_app``. Genuine end-to-end
delivery stays unverified until a real Firebase project exists
(architecture Section 7 item 4).
"""

import inspect
import logging
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from devices.models import DeviceToken
from notifications.services import (
    _get_firebase_app,
    _stringify_data,
    send_push_notification,
)

User = get_user_model()

pytestmark = pytest.mark.django_db

SEND = "notifications.services.messaging.send"
APP = "notifications.services._get_firebase_app"


def _make_user(username):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        account_type="customer",
    )


def _push(user_id, data=None):
    send_push_notification(
        user_id=user_id,
        title="New message",
        body="hello",
        data=data
        or {
            "type": "chat_message",
            "notification_id": 5,
            "deep_link_type": "chat_thread",
            "target_id": 9,
        },
    )


def test_signature_is_unchanged_from_p072_and_p079():
    parameters = list(inspect.signature(send_push_notification).parameters)

    assert parameters == ["user_id", "title", "body", "data"]


def test_sends_one_message_per_registered_token_with_correct_payload():
    user = _make_user("push_two_devices")
    DeviceToken.objects.create(user=user, token="tok-phone", platform="android")
    DeviceToken.objects.create(user=user, token="tok-tablet", platform="ios")
    fake_app = object()

    with patch(APP, return_value=fake_app), patch(SEND) as mock_send:
        _push(user.id)

    assert mock_send.call_count == 2
    sent = {call.args[0].token: call for call in mock_send.call_args_list}
    assert set(sent) == {"tok-phone", "tok-tablet"}
    for call in sent.values():
        message = call.args[0]
        assert message.notification.title == "New message"
        assert message.notification.body == "hello"
        assert message.data == {
            "type": "chat_message",
            "notification_id": "5",
            "deep_link_type": "chat_thread",
            "target_id": "9",
        }
        assert call.kwargs == {"app": fake_app}


def test_only_the_recipients_tokens_are_used():
    recipient = _make_user("push_recipient")
    other = _make_user("push_other")
    DeviceToken.objects.create(user=recipient, token="mine", platform="ios")
    DeviceToken.objects.create(user=other, token="not-mine", platform="ios")

    with patch(APP, return_value=object()), patch(SEND) as mock_send:
        _push(recipient.id)

    assert [c.args[0].token for c in mock_send.call_args_list] == ["mine"]


def test_recipient_without_tokens_is_a_graceful_no_op(caplog):
    user = _make_user("push_no_tokens")

    with patch(APP) as mock_app, patch(SEND) as mock_send:
        with caplog.at_level(logging.INFO, logger="notifications.services"):
            result = _push(user.id)

    assert result is None
    mock_send.assert_not_called()
    mock_app.assert_not_called()
    assert f"user {user.id} has no device tokens" in caplog.text


def test_unconfigured_firebase_skips_without_raising(caplog):
    user = _make_user("push_unconfigured")
    DeviceToken.objects.create(user=user, token="tok-x", platform="android")

    with patch(APP, return_value=None), patch(SEND) as mock_send:
        with caplog.at_level(logging.WARNING, logger="notifications.services"):
            _push(user.id)

    mock_send.assert_not_called()
    assert "Firebase is not configured" in caplog.text


def test_one_failing_token_does_not_block_the_others(caplog):
    user = _make_user("push_partial_failure")
    DeviceToken.objects.create(user=user, token="bad-token-1", platform="android")
    DeviceToken.objects.create(user=user, token="good-token-2", platform="ios")

    def fake_send(message, app=None):
        if message.token == "bad-token-1":
            raise RuntimeError("Requested entity was not found.")
        return "projects/x/messages/1"

    with patch(APP, return_value=object()), patch(SEND, side_effect=fake_send) as m:
        with caplog.at_level(logging.INFO, logger="notifications.services"):
            _push(user.id)

    assert m.call_count == 2
    assert "FCM send failed" in caplog.text
    assert "sent 1/2 push(es)" in caplog.text
    # Only a token PREFIX may be logged, never the full value.
    assert all("bad-token-1" not in r.getMessage() for r in caplog.records)


def test_none_values_are_omitted_and_all_values_are_strings():
    assert _stringify_data(
        {
            "type": "system",
            "notification_id": 7,
            "deep_link_type": "",
            "target_id": None,
        }
    ) == {"type": "system", "notification_id": "7", "deep_link_type": ""}


def test_get_firebase_app_returns_none_without_a_key_path():
    with override_settings(FCM_SERVICE_ACCOUNT_JSON_PATH=""):
        with patch("notifications.services.firebase_admin.get_app") as get_app:
            get_app.side_effect = ValueError("no default app")
            assert _get_firebase_app() is None


def test_get_firebase_app_initialises_from_the_key_path():
    fake_credential, fake_app = MagicMock(), MagicMock()
    with override_settings(FCM_SERVICE_ACCOUNT_JSON_PATH="/secrets/fcm.json"):
        with patch("notifications.services.firebase_admin.get_app") as get_app:
            get_app.side_effect = ValueError("no default app")
            with patch(
                "notifications.services.credentials.Certificate",
                return_value=fake_credential,
            ) as cert:
                with patch(
                    "notifications.services.firebase_admin.initialize_app",
                    return_value=fake_app,
                ) as init:
                    assert _get_firebase_app() is fake_app

    cert.assert_called_once_with("/secrets/fcm.json")
    init.assert_called_once_with(fake_credential)


def test_get_firebase_app_returns_none_when_the_key_file_is_bad():
    with override_settings(FCM_SERVICE_ACCOUNT_JSON_PATH="/does/not/exist.json"):
        with patch("notifications.services.firebase_admin.get_app") as get_app:
            get_app.side_effect = ValueError("no default app")
            assert _get_firebase_app() is None
