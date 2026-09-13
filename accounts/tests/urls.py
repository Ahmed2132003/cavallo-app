"""
Temporary URL conf used ONLY by the test client (Part P-019).

This module is never included from `config/urls.py` — tests point at
it explicitly via `@pytest.mark.urls("accounts.tests.urls")` (the same
mechanism `core/tests/urls.py` established in Part P-012). It exists
solely so `test_permissions.py` can send a real HTTP request through
the full DRF view-dispatch machinery, exercising `HasCapability` as an
actual `permission_classes` entry rather than calling it directly.
"""

from django.urls import path

from accounts.tests.views import ModerationCapabilityTestView

urlpatterns = [
    path("moderation-test/", ModerationCapabilityTestView.as_view()),
]
