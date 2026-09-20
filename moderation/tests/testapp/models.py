from django.db import models

from moderation.models import Moderatable


class DummyContent(Moderatable):
    """
    Minimal concrete model exercising Moderatable end-to-end against a
    real table — same rationale and pattern as core/tests/testapp's
    Widget model for P-011. Not part of production code; only ever
    added to INSTALLED_APPS by config/settings/test.py.

    Deliberately NOT named with a "Test" prefix (e.g. "TestPost") —
    pytest's default collection heuristic tries to collect any class
    named "Test*" as a test class, which would emit a spurious
    PytestCollectionWarning on every test run referencing this model.
    """

    title = models.CharField(max_length=50, default="dummy content")

    class Meta:
        app_label = "moderation_testapp"
