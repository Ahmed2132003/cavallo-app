"""
Data migration (Part P-078): give every EXISTING user a
NotificationPreference row.

The post_save signal only covers users created after this part
shipped. Without this backfill, users who already exist would have no
preference row, and Part P-079's dispatch task would hit
RelatedObjectDoesNotExist on them.

Idempotent: only users that have no row yet get one. Reverse is a
no-op (the rows are harmless and the schema migration's own reverse
drops the table).
"""

from django.conf import settings
from django.db import migrations

BATCH_SIZE = 500


def backfill_notification_preferences(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    NotificationPreference = apps.get_model("notifications", "NotificationPreference")

    users_with_preferences = NotificationPreference.objects.values_list(
        "user_id", flat=True
    )
    missing_user_ids = User.objects.exclude(pk__in=users_with_preferences).values_list(
        "pk", flat=True
    )

    batch = []
    for user_id in missing_user_ids.iterator():
        batch.append(NotificationPreference(user_id=user_id))
        if len(batch) >= BATCH_SIZE:
            NotificationPreference.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        NotificationPreference.objects.bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            backfill_notification_preferences,
            migrations.RunPython.noop,
        ),
    ]
