"""
Production settings.

Same env-required posture as staging (no ALLOWED_HOSTS/CORS default),
plus the strict security headers this part is scoped to add. Anything
beyond these three (HSTS, secure proxy headers, etc.) is intentionally
left for a later, dedicated hardening part rather than guessed at here.
"""

from .base import *  # noqa: F401,F403
from .base import SENTRY_DSN, env

from config.sentry import init_sentry

DEBUG = False

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# Strict security settings (this part's explicit scope).
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Part P-105: real Sentry initialization. An empty SENTRY_DSN disables Sentry.
init_sentry(SENTRY_DSN, environment="prod")
