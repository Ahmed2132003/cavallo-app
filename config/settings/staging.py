"""
Staging settings.

DEBUG is off and ALLOWED_HOSTS/CORS come from real env values — there is
no safe default for either on staging, so both must be set explicitly in
the staging environment's .env (or however env vars are injected there).
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

# No default on purpose: a staging deploy with no ALLOWED_HOSTS set should
# fail loudly (Django raises a clear error) rather than silently accept
# any Host header.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# Part P-105: real Sentry initialization. An empty SENTRY_DSN disables Sentry.
init_sentry(SENTRY_DSN, environment="staging")

# Pre-deploy hardening: access tokens stay valid after a password reset until
# they expire, so refuse to start with a long lifetime.
if JWT_ACCESS_TTL_MINUTES > 15:
    raise ImproperlyConfigured(
        "JWT_ACCESS_TTL_MINUTES must be <= 15 on staging "
        f"(got {JWT_ACCESS_TTL_MINUTES})."
    )

# Pre-deploy hardening: staging is reached through a reverse proxy on the VPS
# (web is published on 127.0.0.1 only, see docker-compose.staging.yml), so
# default to one trusted proxy like prod. Set NUM_PROXIES in .env to override
# (2 if Cloudflare is also in front of that proxy). Too low = every user shares
# the proxy IP; too high = clients can forge X-Forwarded-For. This is a global
# DRF setting: it also fixes the IP that LoginRateThrottle sees.
REST_FRAMEWORK = {**REST_FRAMEWORK, "NUM_PROXIES": _num_proxies(default=1)}