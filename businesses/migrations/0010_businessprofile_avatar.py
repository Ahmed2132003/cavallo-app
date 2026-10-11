from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0009_p101_composite_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="businessprofile",
            name="avatar",
            field=models.FileField(
                blank=True, null=True, upload_to="businesses/avatars/"
            ),
        ),
    ]