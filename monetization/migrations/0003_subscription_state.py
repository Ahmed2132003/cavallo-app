import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("businesses", "0010_businessprofile_avatar"),
        ("monetization", "0002_plan_catalog"),
    ]

    operations = [
        migrations.CreateModel(
            name="Subscription",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("trialing", "Trialing"),
                            ("active", "Active"),
                            ("expired", "Expired"),
                            ("locked", "Locked"),
                        ],
                        default="trialing",
                        max_length=10,
                    ),
                ),
                ("started_at", models.DateTimeField()),
                ("trial_ends_at", models.DateTimeField(blank=True, null=True)),
                ("current_period_start", models.DateTimeField(blank=True, null=True)),
                ("current_period_end", models.DateTimeField(blank=True, null=True)),
                ("quota_bytes", models.PositiveBigIntegerField()),
                ("custom_gb", models.PositiveIntegerField(blank=True, null=True)),
                ("is_featured_effective", models.BooleanField(default=False)),
                ("purge_at", models.DateTimeField(blank=True, null=True)),
                ("canceled_at", models.DateTimeField(blank=True, null=True)),
                (
                    "business",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscription",
                        to="businesses.businessprofile",
                    ),
                ),
                (
                    "pending_plan",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="monetization.plan",
                    ),
                ),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="storage_subscriptions",
                        to="monetization.plan",
                    ),
                ),
            ],
        ),
    ]