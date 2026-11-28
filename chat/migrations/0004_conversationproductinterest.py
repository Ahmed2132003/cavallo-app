import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        # Exact businesses migration name is not in the review bundle;
        # __first__ is enough because only the BusinessProfile table is
        # referenced (it already exists via products' own dependencies).
        ("businesses", "__first__"),
        ("chat", "0003_message_shared_content"),
        ("products", "0004_alter_product_search_vector"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ConversationProductInterest",
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
                    "source",
                    models.CharField(
                        choices=[
                            ("conversation_start", "Conversation start"),
                            ("shared_product", "Shared product"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "first_opened_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "last_opened_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "business",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="product_interests",
                        to="businesses.businessprofile",
                    ),
                ),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="product_interests",
                        to="chat.conversation",
                    ),
                ),
                (
                    "customer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="product_interests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="conversation_interests",
                        to="products.product",
                    ),
                ),
            ],
            options={
                "ordering": ["-last_opened_at"],
            },
        ),
        migrations.AddIndex(
            model_name="conversationproductinterest",
            index=models.Index(
                fields=["business", "product"],
                name="chat_interest_biz_product_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="conversationproductinterest",
            index=models.Index(
                fields=["product", "last_opened_at"],
                name="chat_interest_prod_last_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="conversationproductinterest",
            constraint=models.UniqueConstraint(
                fields=("conversation", "product"),
                name="chat_interest_unique_conv_product",
            ),
        ),
    ]