"""
Auto-creates a NotificationPreference row for every new User
(Part P-078).

Same signal-based pattern as categories/signals.py (P-025) and
moderation/signals.py (P-036): the receiver is wired from
NotificationsConfig.ready(), so no user-creation code path (register
endpoint, createsuperuser, Django Admin, tests) has to remember to
create the row itself.
"""

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from notifications.models import NotificationPreference

User = get_user_model()


@receiver(post_save, sender=User)
def create_notification_preference_for_new_user(
    sender, instance, created, raw=False, **kwargs
):
    """
    On a user's FIRST save only (created=True), create their
    NotificationPreference with every toggle at its default (True).

    * ``raw`` is True while loading fixtures (loaddata); the fixture
      may bring its own rows, so nothing is created then.
    * get_or_create keeps this idempotent: it can never produce a
      second row (user is a OneToOneField) or overwrite an existing
      one.
    """
    if raw or not created:
        return
    NotificationPreference.objects.get_or_create(user=instance)
