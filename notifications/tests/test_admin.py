"""
Admin registration tests for the notifications app (Part P-078).
"""

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from notifications.models import Notification, NotificationPreference

User = get_user_model()

pytestmark = pytest.mark.django_db


def _admin_client():
    superuser = User.objects.create_superuser(
        username="notif_admin",
        email="notif_admin@example.com",
        password="testpass123",
    )
    client = Client()
    client.force_login(superuser)
    return client


def test_notification_is_registered_in_admin():
    assert Notification in admin.site._registry


def test_notification_preference_is_registered_in_admin():
    assert NotificationPreference in admin.site._registry


def test_notification_changelist_loads():
    client = _admin_client()

    response = client.get(reverse("admin:notifications_notification_changelist"))

    assert response.status_code == 200


def test_notification_preference_changelist_loads():
    client = _admin_client()

    response = client.get(
        reverse("admin:notifications_notificationpreference_changelist")
    )

    assert response.status_code == 200
