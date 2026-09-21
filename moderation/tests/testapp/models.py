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


class DummyDeferredContent(Moderatable):
    """
    Same rationale as DummyContent above, but exercises Part P-042's
    ``auto_enqueue_on_create = False`` hook end-to-end against a real
    table, standing in for Reel before Reel exists (P-042's own model
    is the first real, non-throwaway consumer of this hook).

    A test-only model creating its own ModerationQueue row (mirroring
    what content/tasks.py's transcode_reel will do for a real Reel) is
    exercised directly in moderation/tests/test_models.py, not here.
    """

    title = models.CharField(max_length=50, default="dummy deferred content")
    auto_enqueue_on_create = False

    class Meta:
        app_label = "moderation_testapp"