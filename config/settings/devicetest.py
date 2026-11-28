"""
Device-test settings (plain HTTP, no TLS).

Used ONLY by docker-compose.devicetest.yml, to run the backend on a VPS that is
reached by IP address so a real phone can try the app BEFORE a domain and a TLS
certificate exist.

It is config.settings.prod minus the HTTPS forcing:
  - SECURE_SSL_REDIRECT, SESSION_COOKIE_SECURE and CSRF_COOKIE_SECURE are off
    (with them on, every plain-HTTP request would be redirected to HTTPS forever)
  - SECURE_PROXY_SSL_HEADER is not set (there is no TLS in front of nginx)

NEVER use this module for real production. Real production keeps using
config.settings.prod behind TLS (docker-compose.prod.yml + nginx/prod.conf).
"""

from .base import *  # noqa: F401,F403
from .base import SENTRY_DSN, env

from config.sentry import init_sentry

DEBUG = False

# No default on purpose (same posture as staging/prod): must come from .env.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Plain HTTP: these must be off or the Django admin login and every redirect break.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# An empty SENTRY_DSN disables Sentry.
init_sentry(SENTRY_DSN, environment="devicetest")
