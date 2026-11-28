from datetime import timedelta

from django.db import migrations
from django.utils import timezone

# Literals on purpose: migrations stay frozen even if constants change.
_MB = 1024**2


def backfill_free_subscriptions(apps, schema_editor):
    """
    D12: every existing BusinessProfile gets a Free trial starting at
    migration time. Idempotent: businesses that already have a
    subscription are skipped. No-op if the 'free' Plan is missing.
    """
    Plan = apps.get_model("monetization", "Plan")
    Subscription = apps.get_model("monetization", "Subscription")
    BusinessProfile = apps.get_model("businesses", "BusinessProfile")

    free = Plan.objects.filter(code="free").first()
    if free is None:
        return

    now = timezone.now()
    trial_ends_at = now + timedelta(days=free.trial_days or 15)
    purge_at = trial_ends_at + timedelta(days=2)
    quota = free.storage_bytes if free.storage_bytes is not None else 100 * _MB

    have = set(Subscription.objects.values_list("business_id", flat=True))
    # _base_manager: include soft-deleted businesses too.
    missing = [
        pk
        for pk in BusinessProfile._base_manager.values_list("pk", flat=True)
        if pk not in have
    ]
    Subscription.objects.bulk_create(
        [
            Subscription(
                business_id=pk,
                plan=free,
                status="trialing",
                started_at=now,
                trial_ends_at=trial_ends_at,
                purge_at=purge_at,
                quota_bytes=quota,
            )
            for pk in missing
        ],
        batch_size=500,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("monetization", "0003_subscription_state"),
    ]

    operations = [
        migrations.RunPython(backfill_free_subscriptions, migrations.RunPython.noop),
    ]