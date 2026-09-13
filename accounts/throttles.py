"""
Login-specific throttle (Part P-018).

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

from rest_framework.throttling import AnonRateThrottle


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
