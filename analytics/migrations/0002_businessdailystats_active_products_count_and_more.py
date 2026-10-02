from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="businessdailystats",
            name="new_ratings_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="businessdailystats",
            name="average_rating_snapshot",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=3),
        ),
        migrations.AddField(
            model_name="businessdailystats",
            name="active_products_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="businessdailystats",
            name="published_posts_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="businessdailystats",
            name="published_reels_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]