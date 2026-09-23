"""
Development settings.

DJANGO_SETTINGS_MODULE=config.settings.dev is the default the whole dev
Docker Compose stack (and manage.py's own fallback) points at.
"""

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = True

# Permissive-but-explicit: falls back to localhost if ALLOWED_HOSTS isn't
# set in .env, since dev almost always runs against localhost/127.0.0.1.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Dev-only: accept requests from any origin so the Flutter app (emulator,
# simulator, or a real device on the same Wi-Fi) never has to fight CORS
# while iterating locally. Never used in staging/prod.
CORS_ALLOW_ALL_ORIGINS = True

# Never send real email locally — print it to the runserver console instead.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
