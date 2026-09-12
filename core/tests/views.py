"""
Throwaway DRF views used only to exercise `custom_exception_handler`
(Part P-012).

These views are never wired into `config/urls.py` — they only exist to
give the test client something real to hit, via `core/tests/urls.py`
(itself only used by the test suite, see `test_exceptions.py`'s
`@override_settings(ROOT_URLCONF=...)`). No real app endpoint exists
yet, per the part's own note that a temporary throwaway view is fine to
validate a global, generic exception handler against.
"""

from rest_framework.exceptions import (
    AuthenticationFailed,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class ValidationErrorView(APIView):
    """Always raises a field-keyed ValidationError (→ 400)."""

    permission_classes = [AllowAny]

    def get(self, request):
        raise ValidationError({"email": ["This field is required."]})


class PermissionDeniedErrorView(APIView):
    """Always raises PermissionDenied (→ 403)."""

    permission_classes = [AllowAny]

    def get(self, request):
        raise PermissionDenied("You do not have permission to do that.")


class NotFoundErrorView(APIView):
    """Always raises NotFound (→ 404)."""

    permission_classes = [AllowAny]

    def get(self, request):
        raise NotFound("No such widget.")


class AuthenticationFailedErrorView(APIView):
    """Always raises AuthenticationFailed (→ 401)."""

    permission_classes = [AllowAny]

    def get(self, request):
        raise AuthenticationFailed("Invalid credentials.")


class UnhandledErrorView(APIView):
    """
    Always raises a plain, non-DRF exception (→ unhandled by DRF).

    DRF's default exception_handler returns None for this, so
    Django's normal 500 machinery takes over (debug traceback page
    in DEBUG=True, or Django's own bare 500 in DEBUG=False) — this
    view exists to prove `custom_exception_handler` doesn't swallow
    or otherwise interfere with that path.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        raise RuntimeError("Deliberately unhandled for P-012 testing.")


class OkView(APIView):
    """A trivially successful view — confirms the handler is a no-op
    on the happy path (it should never be invoked at all)."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"ok": True})
