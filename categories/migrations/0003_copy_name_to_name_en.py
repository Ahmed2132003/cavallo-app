from django.db import migrations
from django.db.models import F


def copy_name_to_name_en(apps, schema_editor):
    """Part P-112: every existing category keeps its name as the English one."""
    Category = apps.get_model("categories", "Category")
    Category.objects.filter(name_en="").update(name_en=F("name"))


class Migration(migrations.Migration):

    dependencies = [
        ("categories", "0002_category_localized_names"),
    ]

    operations = [
        # Reverse is a no-op: ``name`` was never touched, and dropping the
        # column in the previous migration discards ``name_en`` anyway.
        migrations.RunPython(copy_name_to_name_en, migrations.RunPython.noop),
    ]
