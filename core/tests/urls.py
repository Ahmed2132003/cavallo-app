"""
Temporary URL conf used ONLY by the test client (Part P-012).

This module is never included from `config/urls.py` — tests point at
it explicitly via `@override_settings(ROOT_URLCONF="core.tests.urls")`.
It exists solely so `test_exceptions.py` can send a real HTTP request
through the full DRF view-dispatch machinery (not just call the view
function directly), which is what actually exercises the
EXCEPTION_HANDLER setting.
"""

from django.urls import path

from core.tests.views import (
    AuthenticationFailedErrorView,
    NotFoundErrorView,
    OkView,
    PermissionDeniedErrorView,
    UnhandledErrorView,
    ValidationErrorView,
)

urlpatterns = [
    path("ok/", OkView.as_view()),
    path("validation-error/", ValidationErrorView.as_view()),
    path("auth-failed/", AuthenticationFailedErrorView.as_view()),
    path("permission-denied/", PermissionDeniedErrorView.as_view()),
    path("not-found/", NotFoundErrorView.as_view()),
    path("unhandled/", UnhandledErrorView.as_view()),
]
