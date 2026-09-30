"""
Tests for notifications.tasks.enqueue_notification (Part P-079).

dispatch_delay is the autouse fixture from the root conftest.py (the
mocked dispatch_notification.delay).
"""

from notifications.tasks import enqueue_notification


def test_enqueue_forwards_kwargs_to_delay(dispatch_delay):
    enqueue_notification(
        recipient_id=1,
        notification_type="new_follower",
        title="T",
        body="B",
        deep_link_type="business_profile",
        target_id=5,
    )

    dispatch_delay.assert_called_once_with(
        recipient_id=1,
        notification_type="new_follower",
        title="T",
        body="B",
        deep_link_type="business_profile",
        target_id=5,
    )


def test_enqueue_swallows_broker_errors(dispatch_delay):
    dispatch_delay.side_effect = RuntimeError("redis down")

    # Must not raise.
    enqueue_notification(
        recipient_id=1,
        notification_type="new_follower",
        title="T",
        body="B",
    )

    dispatch_delay.assert_called_once()
