from django.db import migrations, models

# Literals on purpose: migrations must stay frozen even if constants change.
_MB = 1024**2
_GB = 1024**3

CATALOG = [
    {
        "code": "free", "name_ar": "مجانية", "name_en": "Free",
        "price_egp": "0.00", "storage_bytes": 100 * _MB,
        "is_featured": False, "trial_days": 15, "is_custom": False,
        "sort_order": 10,
    },
    {
        "code": "pro", "name_ar": "برو", "name_en": "Pro",
        "price_egp": "99.00", "storage_bytes": 1 * _GB,
        "is_featured": False, "trial_days": 0, "is_custom": False,
        "sort_order": 20,
    },
    {
        "code": "max", "name_ar": "ماكس", "name_en": "Max",
        "price_egp": "199.00", "storage_bytes": 3 * _GB,
        "is_featured": False, "trial_days": 0, "is_custom": False,
        "sort_order": 30,
    },
    {
        "code": "ultra", "name_ar": "ألترا", "name_en": "Ultra",
        "price_egp": "449.00", "storage_bytes": 10 * _GB,
        "is_featured": True, "trial_days": 0, "is_custom": False,
        "sort_order": 40,
    },
    {
        "code": "infinity", "name_ar": "إنفينيتي", "name_en": "Infinity",
        "price_egp": None, "storage_bytes": None,
        "is_featured": False, "trial_days": 0, "is_custom": True,
        "sort_order": 50,
    },
]


def seed_plans(apps, schema_editor):
    """Idempotent: get_or_create by code; never overwrites existing rows."""
    from decimal import Decimal

    Plan = apps.get_model("monetization", "Plan")
    for row in CATALOG:
        data = dict(row)
        code = data.pop("code")
        price = Decimal(data["price_egp"]) if data["price_egp"] is not None else None
        data["price_egp"] = price
        Plan.objects.get_or_create(
            code=code,
            defaults={
                **data,
                "name": data["name_en"],
                # Legacy NOT NULL columns: monthly billing period, EGP.
                "duration_days": 30,
                "price": price if price is not None else Decimal("0.00"),
                "currency": "EGP",
                "is_active": True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("monetization", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="code",
            field=models.CharField(
                blank=True,
                choices=[
                    ("free", "Free"),
                    ("pro", "Pro"),
                    ("max", "Max"),
                    ("ultra", "Ultra"),
                    ("infinity", "Infinity"),
                ],
                max_length=20,
                null=True,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="plan",
            name="name_ar",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="plan",
            name="name_en",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="plan",
            name="price_egp",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True
            ),
        ),
        migrations.AddField(
            model_name="plan",
            name="storage_bytes",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="plan",
            name="is_featured",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="plan",
            name="trial_days",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="plan",
            name="is_custom",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="plan",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="plan",
            name="sort_order",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddConstraint(
            model_name="plan",
            constraint=models.CheckConstraint(
                condition=models.Q(("trial_days", 0), ("code", "free"), _connector="OR"),
                name="plan_trial_days_only_free",
            ),
        ),
        migrations.RunPython(seed_plans, migrations.RunPython.noop),
    ]