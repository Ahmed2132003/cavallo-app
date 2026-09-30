"""
Signal tests for notifications.signals (Part P-078).

Every assertion here queries NotificationPreference directly after the
user is created. No test creates a preference row by hand - that
would prove nothing about the signal.
"""

import pytest
from django.contrib.auth import get_user_model

from notifications.models import NotificationPreference

User = get_user_model()

pytestmark = pytest.mark.django_db


def _make_user(username):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        account_type="customer",
    )


def test_new_user_gets_exactly_one_preference_row_with_defaults_true():
    user = _make_user("signal_user")

    rows = NotificationPreference.objects.filter(user_id=user.id)

    assert rows.count() == 1
    preference = rows.get()
    assert preference.chat_notifications_enabled is True
    assert preference.moderation_notifications_enabled is True
    assert preference.social_notifications_enabled is True


def test_business_user_also_gets_a_preference_row():
    user = User.objects.create_user(
        username="signal_business",
        email="signal_business@example.com",
        password="testpass123",
        account_type="business",
    )

    assert NotificationPreference.objects.filter(user_id=user.id).count() == 1


def test_superuser_gets_a_preference_row():
    user = User.objects.create_superuser(
        username="signal_admin",
        email="signal_admin@example.com",
        password="testpass123",
    )

    assert NotificationPreference.objects.filter(user_id=user.id).count() == 1


def test_each_user_gets_their_own_row():
    first = _make_user("signal_first")
    second = _make_user("signal_second")

    first_row = NotificationPreference.objects.get(user_id=first.id)
    second_row = NotificationPreference.objects.get(user_id=second.id)

    assert first_row.pk != second_row.pk


def test_resaving_an_existing_user_creates_no_second_row_and_keeps_toggles():
    user = _make_user("signal_resave")
    preference = NotificationPreference.objects.get(user_id=user.id)
    preference.chat_notifications_enabled = False
    preference.save()

    user.first_name = "Changed"
    user.save()

    rows = NotificationPreference.objects.filter(user_id=user.id)
    assert rows.count() == 1
    assert rows.get().chat_notifications_enabled is False
