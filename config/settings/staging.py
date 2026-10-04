"""
Staging settings.

DEBUG is off and ALLOWED_HOSTS/CORS come from real env values — there is
no safe default for either on staging, so both must be set explicitly in
the staging environment's .env (or however env vars are injected there).
"""

from .base import *  # noqa: F401,F403
from .base import SENTRY_DSN, env

from config.sentry import init_sentry

DEBUG = False

# No default on purpose: a staging deploy with no ALLOWED_HOSTS set should
# fail loudly (Django raises a clear error) rather than silently accept
# any Host header.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# Part P-105: real Sentry initialization. An empty SENTRY_DSN disables Sentry.
init_sentry(SENTRY_DSN, environment="staging")
