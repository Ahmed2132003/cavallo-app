"""
Production settings.

Same env-required posture as staging (no ALLOWED_HOSTS/CORS default),
plus the strict security headers this part is scoped to add. Part P-106 adds
SECURE_PROXY_SSL_HEADER (TLS terminates at Nginx). HSTS is still left for an
owner decision rather than guessed at here.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import (
    JWT_ACCESS_TTL_MINUTES,
    REST_FRAMEWORK,
    SENTRY_DSN,
    _num_proxies,
    env,
)

from config.sentry import init_sentry

DEBUG = False

# Pre-deploy hardening: access tokens stay valid after a password reset until
# they expire, so refuse to start with a long lifetime.
if JWT_ACCESS_TTL_MINUTES > 15:
    raise ImproperlyConfigured(
        "JWT_ACCESS_TTL_MINUTES must be <= 15 on prod "
        f"(got {JWT_ACCESS_TTL_MINUTES})."
    )

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

# Task P2.5: one trusted proxy (Nginx) in front of Django, so the client IP is
# the LAST X-Forwarded-For entry (the one Nginx appended). Set NUM_PROXIES in
# .env to override (e.g. 2 if Cloudflare, or another proxy because HTTP_PORT /
# HTTPS_PORT are remapped, is in front of Nginx). Too low = every user shares
# one IP; too high = clients can forge X-Forwarded-For.
# This is a global DRF setting: it also fixes the IP that LoginRateThrottle sees.
REST_FRAMEWORK = {**REST_FRAMEWORK, "NUM_PROXIES": _num_proxies(default=1)}

# Part P-105: real Sentry initialization. An empty SENTRY_DSN disables Sentry.
init_sentry(SENTRY_DSN, environment="prod")