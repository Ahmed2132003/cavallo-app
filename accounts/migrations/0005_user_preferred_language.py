from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_user_following_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="preferred_language",
            field=models.CharField(
                choices=[("ar", "Arabic"), ("en", "English")],
                default="ar",
                help_text="UI and notification language (ar or en).",
                max_length=2,
            ),
        ),
    ]
