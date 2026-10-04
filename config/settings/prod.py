"""
Production settings.

Same env-required posture as staging (no ALLOWED_HOSTS/CORS default),
plus the strict security headers this part is scoped to add. Part P-106 adds
SECURE_PROXY_SSL_HEADER (TLS terminates at Nginx). HSTS is still left for an
owner decision rather than guessed at here.
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

# Part P-106: TLS terminates at Nginx, so Django only ever sees plain HTTP from
# the proxy. Without this, SECURE_SSL_REDIRECT would redirect every proxied
# request to HTTPS forever. Nginx always overwrites X-Forwarded-Proto
# (nginx/snippets/cavallo_locations.conf). Only safe because web and asgi are
# reachable ONLY through Nginx, never published on a public interface.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Part P-105: real Sentry initialization. An empty SENTRY_DSN disables Sentry.
init_sentry(SENTRY_DSN, environment="prod")
