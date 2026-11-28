import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("businesses", "0010_businessprofile_avatar"),
        ("monetization", "0004_backfill_free_subscriptions"),
    ]

    operations = [
        migrations.CreateModel(
            name="StoredFile",
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
                ("model", models.CharField(max_length=60)),
                ("object_id", models.PositiveBigIntegerField()),
                ("field", models.CharField(max_length=30)),
                ("file_name", models.CharField(max_length=255)),
                ("size_bytes", models.PositiveBigIntegerField(default=0)),
                (
                    "business",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stored_files",
                        to="businesses.businessprofile",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="storedfile",
            constraint=models.UniqueConstraint(
                fields=("model", "object_id", "field"),
                name="uniq_stored_file_slot",
            ),
        ),
        migrations.AddIndex(
            model_name="storedfile",
            index=models.Index(fields=["file_name"], name="storedfile_name_idx"),
        ),
    ]