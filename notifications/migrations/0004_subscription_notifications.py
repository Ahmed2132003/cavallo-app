from django.db import migrations, models


class Migration(migrations.Migration):
    """BE-11: choices only (new 'subscription' type + 'subscription_plans' link).
    No schema change, safe on a populated table."""

    dependencies = [
        ("notifications", "0003_p112_localization"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="deep_link_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("business_profile", "Business profile"),
                    ("post_detail", "Post detail"),
                    ("reel_detail", "Reel detail"),
                    ("product_detail", "Product detail"),
                    ("chat_thread", "Chat thread"),
                    ("subscription_plans", "Subscription plans"),
                ],
                default="",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="notification",
            name="notification_type",
            field=models.CharField(
                choices=[
                    ("chat_message", "Chat message"),
                    ("moderation_approved", "Moderation approved"),
                    ("moderation_rejected", "Moderation rejected"),
                    ("new_follower", "New follower"),
                    ("comment_on_content", "Comment on content"),
                    ("new_like", "New like"),
                    ("new_share", "New share"),
                    ("new_rating", "New rating"),
                    ("system_announcement", "System announcement"),
                    ("subscription", "Subscription"),
                ],
                max_length=32,
            ),
        ),
    ]