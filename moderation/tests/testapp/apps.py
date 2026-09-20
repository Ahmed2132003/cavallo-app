from django.apps import AppConfig


class TestAppConfig(AppConfig):
    """
    Throwaway app providing a concrete Moderatable model (TestPost) for
    exercising the enqueue-on-creation signal end-to-end against a real
    table — same rationale and pattern as core/tests/testapp for P-011.

    Only ever added to INSTALLED_APPS by config/settings/test.py — never
    by base.py/dev.py/staging.py/prod.py, so this never ships and never
    needs its own migrations (pytest-django creates its table directly,
    equivalent to `migrate --run-syncdb`, since it has no migrations
    module).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "moderation.tests.testapp"
    label = "moderation_testapp"
