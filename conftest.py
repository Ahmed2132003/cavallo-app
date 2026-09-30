"""
Project-wide pytest fixtures (Part P-079).

pytest runs against config.settings.test, which inherits dev's real
Redis broker and does NOT enable Celery eager mode. Without this
fixture, any test that triggers a notification source (moderation
decision, follow, comment, chat offline fallback) would publish a real
dispatch_notification message to Redis, and the running docker worker
could execute it against the dev database.

The autouse fixture below replaces dispatch_notification.delay with a
mock for EVERY test. Tests that want to assert a source dispatched a
notification request the fixture by name (dispatch_delay) and assert on
it. Tests that exercise the task body call
notifications.tasks.dispatch_notification(...) directly, which this
patch does not affect.
"""

from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def dispatch_delay():
    from notifications.tasks import dispatch_notification

    with mock.patch.object(dispatch_notification, "delay") as mocked_delay:
        yield mocked_delay
