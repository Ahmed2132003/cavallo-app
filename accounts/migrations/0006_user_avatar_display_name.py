from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_user_preferred_language"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="avatar",
            field=models.FileField(blank=True, null=True, upload_to="avatars/"),
        ),
        migrations.AddField(
            model_name="user",
            name="display_name",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
    ]
