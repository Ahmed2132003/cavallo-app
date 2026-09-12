"""
Django settings for config project (Social Commerce Discovery Platform).

Part P-010 scope: base.py holds every setting that does NOT vary between
dev/staging/prod. Each environment module (dev.py, staging.py, prod.py)
does `from .base import *` and overrides only what differs for that
environment (DEBUG, ALLOWED_HOSTS, CORS, and prod's security headers).

Architecture rule: no app-specific business settings here — this file is
pure Django/DRF/Channels/Celery framework wiring. Project apps are added
to INSTALLED_APPS starting with the part that creates them (the first is
`core`, added in Part P-011).

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/
"""

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import environ

# config/settings/base.py -> config/settings/ -> config/ -> repo root.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)
# Read .env for local (non-Docker) runs. Inside Docker Compose, real env
# vars are injected by the "env_file: .env" directive instead, and this
# call is a harmless no-op if the file isn't present.
environ.Env.read_env(BASE_DIR / ".env")


# ---------------------------------------------------------------------------
# Core / security
# ---------------------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY")

# DEBUG and ALLOWED_HOSTS are environment-specific — set in dev.py /
# staging.py / prod.py, not here.


# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "channels",
    # Local apps
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# ---------------------------------------------------------------------------
# Database — parsed from DATABASE_URL (see .env.example)
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db("DATABASE_URL"),
}


# ---------------------------------------------------------------------------
# Cache / Channels layer — both point at the same Redis instance in dev
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL")


def _redis_url_with_db(base_url, db_index):
    """
    Return a copy of a Redis URL with its logical DB index replaced.

    Rebuilds the URL's path as "/{db_index}" via urllib.parse rather than
    string-splicing, so this is robust regardless of whether base_url
    already ends in "/N" or has no path/auth/query component at all.
    """
    parts = urlsplit(base_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{db_index}", parts.query, parts.fragment))


# ---------------------------------------------------------------------------
# Cache — Part P-014. django-redis, pointed at a Redis DB index distinct
# from Celery's broker/result backend (CELERY_BROKER_URL below), so cache
# keys and Celery's broker/task metadata never collide in the same Redis
# keyspace.
#
# Redis logical DB index convention for this project (also documented in
# .env.example and CONFIG.md):
#   DB 0  — Celery broker/result backend, and (for now) the Channels layer
#           too — both still point at plain REDIS_URL, unchanged by this
#           part.
#   DB 1  — Django cache framework (this part). Index is read from
#           REDIS_CACHE_DB rather than hardcoded, per this part's scope.
#   DB 2+ — reserved. In particular, Phase 12's Channels layer work
#           should give CHANNEL_LAYERS its own dedicated index (e.g. DB 2)
#           instead of continuing to share DB 0 with Celery — this part's
#           scope was the cache framework only, so CHANNEL_LAYERS below is
#           intentionally left untouched.
# ---------------------------------------------------------------------------
REDIS_CACHE_DB = env.int("REDIS_CACHE_DB", default=1)
REDIS_CACHE_URL = _redis_url_with_db(REDIS_URL, REDIS_CACHE_DB)

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_CACHE_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    }
}


# ---------------------------------------------------------------------------
# Celery — broker/result backend both on Redis in dev
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"


# ---------------------------------------------------------------------------
# DRF / JWT
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    # Part P-012: every DRF error response is reshaped into the
    # {"error": {"code", "message", "fields"}} envelope Flutter's P-004
    # error interceptor expects. This shape is a locked contract — see
    # core/exceptions.py's module docstring before changing it.
    "EXCEPTION_HANDLER": "core.exceptions.custom_exception_handler",
}


# ---------------------------------------------------------------------------
# Sentry — no-op unless SENTRY_DSN is set. Already env-gated, so it's safe
# to leave here rather than duplicating the init call in staging.py/prod.py;
# dev stays a no-op (SENTRY_DSN is blank in .env.example), staging/prod
# activate it once a real SENTRY_DSN value is provided (see P-003 handoff —
# the real sentry_sdk configuration/tuning happens in P-024).
# ---------------------------------------------------------------------------
SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.0)


# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"  # noqa: E501
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Static / media
# Architecture rule: no local-disk media storage, ever — not even as a
# dev placeholder. Part P-013 wires the "default" (media) storage to a
# provider-agnostic S3-compatible backend (core/storage_backends.py),
# reading connection details from the OBJECT_STORAGE_* env vars below.
# In dev/test these default to the MinIO container added in P-013's
# docker-compose.yml; staging/prod will point at a real provider once
# architecture Section 7 item 2 is resolved — a pure env-var change,
# no code change, since core/storage_backends.py reads only these vars.
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

OBJECT_STORAGE_PROVIDER = env("OBJECT_STORAGE_PROVIDER", default="")
OBJECT_STORAGE_BUCKET = env("OBJECT_STORAGE_BUCKET", default="")
OBJECT_STORAGE_KEY = env("OBJECT_STORAGE_KEY", default="")
OBJECT_STORAGE_SECRET = env("OBJECT_STORAGE_SECRET", default="")
OBJECT_STORAGE_REGION = env("OBJECT_STORAGE_REGION", default="")
OBJECT_STORAGE_ENDPOINT_URL = env("OBJECT_STORAGE_ENDPOINT_URL", default=None)
OBJECT_STORAGE_USE_SSL = env.bool("OBJECT_STORAGE_USE_SSL", default=True)

STORAGES = {
    "default": {
        "BACKEND": "core.storage_backends.MediaStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"