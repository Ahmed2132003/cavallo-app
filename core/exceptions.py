"""
Custom DRF exception handler + unified error envelope (Part P-012).

Architecture Section 8 mandates one consistent error shape across the
entire API. Flutter's network layer (Part P-004, `error_interceptor.dart`)
was already built assuming this exact envelope exists:

    {"error": {"code": "VALIDATION_ERROR", "message": "...",
               "fields": {"email": ["already exists"]}}}

This shape is now a contract. Do NOT change it later without updating
the Flutter error interceptor in the same change.

Every future serializer/view should just raise the standard DRF
exceptions (ValidationError, PermissionDenied, NotFound,
AuthenticationFailed, NotAuthenticated, Throttled, MethodNotAllowed,
...) and trust they come out in the correct shape below — no future
part should build its own custom error response.
"""

from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

# Maps a DRF/APIException's default_code (or a manual fallback below) to
# the stable, Flutter-facing "code" string. Deliberately explicit rather
# than reusing DRF's internal default_code values verbatim, since those
# are an implementation detail DRF could change independently of this
# contract.
_EXCEPTION_CODE_MAP = {
    "authentication_failed": "AUTHENTICATION_FAILED",
    "not_authenticated": "AUTHENTICATION_FAILED",
    "permission_denied": "PERMISSION_DENIED",
    "not_found": "NOT_FOUND",
    "method_not_allowed": "METHOD_NOT_ALLOWED",
    "not_acceptable": "NOT_ACCEPTABLE",
    "unsupported_media_type": "UNSUPPORTED_MEDIA_TYPE",
    "throttled": "THROTTLED",
    "parse_error": "PARSE_ERROR",
    # Part P-018: rest_framework_simplejwt's InvalidToken/TokenError
    # (raised by RefreshView for an expired/garbage/blacklisted refresh
    # token) carries this code — see _flatten_code()'s docstring below
    # for why it needs its own entry rather than falling through to a
    # dict lookup crash.
    "token_not_valid": "AUTHENTICATION_FAILED",
}

# Human-readable fallback messages, used only when the exception itself
# didn't carry a usable message (see _extract_message below).
_DEFAULT_MESSAGES = {
    "AUTHENTICATION_FAILED": "Authentication credentials were not provided or are invalid.",  # noqa: E501
    "PERMISSION_DENIED": "You do not have permission to perform this action.",
    "NOT_FOUND": "The requested resource was not found.",
    "METHOD_NOT_ALLOWED": "This method is not allowed on this endpoint.",
    "NOT_ACCEPTABLE": "Could not satisfy the request's Accept header.",
    "UNSUPPORTED_MEDIA_TYPE": "Unsupported media type.",
    "THROTTLED": "Request was throttled.",
    "PARSE_ERROR": "Malformed request.",
    "VALIDATION_ERROR": "Invalid input.",
    "SERVER_ERROR": "An unexpected error occurred.",
    "ERROR": "An error occurred.",
}


def _extract_message(exc, code):
    """
    Pull a single human-readable message out of a DRF exception.

    exc.detail can be a plain ErrorDetail, a list of them, or (for
    ValidationError raised with a dict) a dict of field -> [errors].
    For the non-validation cases we just want one sentence, not the
    full structure (that's what `fields` is for).
    """
    detail = getattr(exc, "detail", None)

    if isinstance(detail, str):
        text = detail
    elif isinstance(detail, list) and detail:
        text = str(detail[0])
    elif isinstance(detail, dict) and detail:
        # Non-field-keyed dict details (rare, but possible) — flatten to
        # the first value found.
        first_value = next(iter(detail.values()))
        if isinstance(first_value, list) and first_value:
            text = str(first_value[0])
        else:
            text = str(first_value)
    else:
        text = None

    if not text:
        text = _DEFAULT_MESSAGES.get(code, _DEFAULT_MESSAGES["ERROR"])
    return text


def _extract_fields(exc):
    """
    Return the per-field error dict for a ValidationError raised with a
    dict of field -> [errors] (the normal case for serializer.is_valid()).
    Returns {} for anything else, per the envelope contract ("fields":
    omitted/empty when not a field-keyed validation error).
    """
    if not isinstance(exc, ValidationError):
        return {}

    detail = exc.detail
    if not isinstance(detail, dict):
        return {}

    fields = {}
    for field_name, errors in detail.items():
        if isinstance(errors, list):
            fields[field_name] = [str(e) for e in errors]
        else:
            fields[field_name] = [str(errors)]
    return fields


def _flatten_code(raw_code):
    """
    Reduce a DRF get_codes() result to a single leaf code string.

    Bug found and fixed during Part P-018 (surfaced by real API tests
    against RefreshView, not assumed): get_codes() mirrors the *shape*
    of exc.detail, not just its content. For a plain APIException,
    detail is a string and get_codes() returns a string — the only case
    this function originally handled. But rest_framework_simplejwt's
    InvalidToken (raised by TokenRefreshView for an expired, malformed,
    or blacklisted refresh token — exactly what Part P-018's rotation/
    reuse-detection deliberately triggers) has a *dict*-shaped detail
    (``{"detail": ..., "code": "token_not_valid"}``), so get_codes()
    returns a dict too. Passing that dict straight into
    ``_EXCEPTION_CODE_MAP.get(raw_code, ...)`` crashed with
    ``TypeError: unhashable type: 'dict'`` — a real 500 on every
    invalid-refresh-token request, which would have made P-018's own
    "reused refresh token -> 401" acceptance criterion impossible to
    satisfy correctly.

    This walks the (possibly nested) structure down to one
    representative leaf string: prefers an explicit "code" key when the
    dict has one (simplejwt's own shape), otherwise takes the first
    value found; unwraps a non-empty list/tuple to its first element the
    same way. `message`/`fields` already carry the full detail
    elsewhere in this module — this function only needs one string for
    the top-level `code`.
    """
    if isinstance(raw_code, dict):
        if "code" in raw_code:
            return _flatten_code(raw_code["code"])
        return _flatten_code(next(iter(raw_code.values()), None))
    if isinstance(raw_code, (list, tuple)):
        return _flatten_code(raw_code[0]) if raw_code else None
    return raw_code


def custom_exception_handler(exc, context):
    """
    DRF EXCEPTION_HANDLER entry point.

    Calls DRF's default handler first (it already knows how to turn an
    APIException into a Response with the right status code); if it
    returns something, this function only reshapes `response.data` into
    the {"error": {...}} envelope. If DRF's default handler returns
    None (an exception it doesn't recognize as an APIException — i.e.
    a genuinely unhandled exception), this function also returns None,
    letting Django's normal 500 machinery proceed (see
    config.settings.base / DEBUG for the dev-vs-prod behavior split,
    handled by Django itself via DEBUG, not by this function).
    """
    response = drf_exception_handler(exc, context)

    if response is None:
        # Not a recognized APIException — a genuinely unhandled
        # exception. DRF itself would otherwise just re-raise this up
        # into Django's normal exception machinery.
        if settings.DEBUG:
            # Preserve Django's own debug traceback page unchanged, so
            # debugging isn't made harder — let it propagate exactly
            # as DRF's default handler would.
            return None

        # DEBUG=False: never let a stack trace leak to the client.
        # Return the same envelope shape as every other error, with a
        # generic, non-revealing message.
        return Response(
            {
                "error": {
                    "code": "SERVER_ERROR",
                    "message": _DEFAULT_MESSAGES.get(
                        "SERVER_ERROR", "An unexpected error occurred."
                    ),
                    "fields": {},
                }
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if isinstance(exc, ValidationError):
        code = "VALIDATION_ERROR"
    else:
        # get_codes() is the documented, stable way to read an
        # APIException's code (falls back to `default_code` for
        # anything that predates/skips get_codes()). For non-validation
        # exceptions this is always a plain string, never nested.
        get_codes = getattr(exc, "get_codes", None)
        raw_code = get_codes() if callable(get_codes) else None
        if raw_code is None:
            raw_code = getattr(exc, "default_code", None)
        raw_code = _flatten_code(raw_code)
        code = _EXCEPTION_CODE_MAP.get(raw_code, "ERROR")

    message = _extract_message(exc, code)
    fields = _extract_fields(exc)

    response.data = {
        "error": {
            "code": code,
            "message": message,
            "fields": fields,
        }
    }
    return response
