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
