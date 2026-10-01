import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("businesses", "0008_businessprofile_average_rating_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="BusinessDailyStats",
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
                ("date", models.DateField()),
                ("new_followers", models.PositiveIntegerField(default=0)),
                ("total_likes_received", models.PositiveIntegerField(default=0)),
                ("total_comments_received", models.PositiveIntegerField(default=0)),
                ("total_story_views", models.PositiveIntegerField(default=0)),
                (
                    "business",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="daily_stats",
                        to="businesses.businessprofile",
                    ),
                ),
            ],
            options={
                "unique_together": {("business", "date")},
            },
        ),
    ]