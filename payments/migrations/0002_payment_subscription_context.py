from django.db import migrations, models


class Migration(migrations.Migration):
    """BE-07: payment context. Safe on a populated DB (default + NULL)."""

    dependencies = [
        ("payments", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscription",
            name="kind",
            field=models.CharField(
                choices=[("featured", "Featured"), ("storage", "Storage plan")],
                default="featured",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="subscription",
            name="gb",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=8, null=True
            ),
        ),
    ]