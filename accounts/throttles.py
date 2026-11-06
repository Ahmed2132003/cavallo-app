"""
Throttles for the accounts app: login (Part P-018) and password reset
(Task P2.5).

Architecture Section 10 names the login endpoint's throttle as one of
the two most important throttling points in the whole API ("أهم نقطتين
هنا: login endpoint ضد brute force"). This class exists so that strict
rate limiting can be attached to accounts.views.LoginView specifically
(via its own throttle_classes) WITHOUT touching
DEFAULT_THROTTLE_CLASSES in REST_FRAMEWORK — no other endpoint in the
project is affected by this scope. General API-wide throttling for
every other endpoint is a separate, later, Phase-wide concern per this
part's own scope note.

The actual rate ("5/min") lives in REST_FRAMEWORK["DEFAULT_THROTTLE_
RATES"]["login"] (config/settings/base.py), not hardcoded here, so it
can be tuned without touching this file.
"""

import hashlib
from collections.abc import Mapping

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle

from accounts import services


class LoginRateThrottle(AnonRateThrottle):
    """
    Scoped throttle for POST /api/v1/auth/login/ only.

    Deliberately an AnonRateThrottle subclass (keyed by client IP, via
    DRF's own get_ident()) rather than UserRateThrottle: a brute-force
    attacker hitting /login/ is by definition not yet authenticated, so
    there is no request.user to key on — IP is the only identity DRF
    has at this point in the request.
    """

    scope = "login"


# ---------------------------------------------------------------------------
# Password reset throttles (Task P2.5)
#
# Every password reset endpoint (request, verify, confirm) gets two layers:
#   - per client IP        (get_ident(), honours REST_FRAMEWORK NUM_PROXIES)
#   - per identifier       (the email from the request body)
# Each layer has its own scope, so no two layers or endpoints share a
# counter. Rates are read from Django settings at request time (same
# precedent as reports.throttles.ReportRateThrottle), so they can be tuned
# per environment or overridden in tests without touching this file.
#
# Anti-enumeration: nothing here touches the database or checks whether the
# account exists. The identifier key depends only on the submitted string,
# so an unknown email is counted and throttled exactly like a real one, and
# the 429 answer is the same for both. The email is normalized with the
# same function the OTP/token keys use (services._normalize_email) and then
# SHA-256 hashed; a raw email never appears in a cache key.
# ---------------------------------------------------------------------------


class _PasswordResetThrottle(SimpleRateThrottle):
    """
    Base for the password reset throttles. Subclasses set `scope` and
    `rate_setting` (the name of the Django setting holding "N/period").
    """

    rate_setting = None

    def get_rate(self):
        return getattr(settings, self.rate_setting)

    def _key_for(self, ident):
        return self.cache_format % {"scope": self.scope, "ident": ident}


class _PasswordResetIPThrottle(_PasswordResetThrottle):
    def get_cache_key(self, request, view):
        return self._key_for(self.get_ident(request))


class _PasswordResetIdentifierThrottle(_PasswordResetThrottle):
    @staticmethod
    def _identifier_hash(request):
        data = request.data
        if not isinstance(data, Mapping):
            return None
        email = data.get("email")
        if not isinstance(email, str):
            return None
        normalized = services._normalize_email(email)
        if not normalized:
            return None
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def get_cache_key(self, request, view):
        ident = self._identifier_hash(request)
        if ident is None:
            # No usable identifier (the serializer will answer 400 without
            # doing any work); the per-IP layer still counts this request.
            return None
        return self._key_for(ident)


class PasswordResetRequestIPThrottle(_PasswordResetIPThrottle):
    scope = "password_reset_request_ip"
    rate_setting = "PASSWORD_RESET_REQUEST_IP_RATE"


class PasswordResetRequestIdentifierThrottle(_PasswordResetIdentifierThrottle):
    scope = "password_reset_request_identifier"
    rate_setting = "PASSWORD_RESET_REQUEST_IDENTIFIER_RATE"


class PasswordResetVerifyIPThrottle(_PasswordResetIPThrottle):
    scope = "password_reset_verify_ip"
    rate_setting = "PASSWORD_RESET_VERIFY_IP_RATE"


class PasswordResetVerifyIdentifierThrottle(_PasswordResetIdentifierThrottle):
    scope = "password_reset_verify_identifier"
    rate_setting = "PASSWORD_RESET_VERIFY_IDENTIFIER_RATE"


class PasswordResetConfirmIPThrottle(_PasswordResetIPThrottle):
    scope = "password_reset_confirm_ip"
    rate_setting = "PASSWORD_RESET_CONFIRM_IP_RATE"


class PasswordResetConfirmIdentifierThrottle(_PasswordResetIdentifierThrottle):
    scope = "password_reset_confirm_identifier"
    rate_setting = "PASSWORD_RESET_CONFIRM_IDENTIFIER_RATE"