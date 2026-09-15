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
    # Part P-018: required for BLACKLIST_AFTER_ROTATION=True below, and
    # for LogoutView's explicit RefreshToken(...).blacklist() call.
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "channels",
    # Local apps
    "core",
    "accounts",
    "businesses",
]

# ---------------------------------------------------------------------------
# Custom user model — Part P-016. MUST be set before the first `migrate`
# ever runs against a database: swapping AUTH_USER_MODEL after real
# migrations/data exist is extremely painful, which is why accounts is
# the very first Phase 3 part, before anything else references User.
# See accounts/models.py's module docstring for the full field-to-role
# mapping (account_type, is_staff, is_superuser, is_moderator,
# is_business_verified) that Part P-019's permission system builds on.
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Part P-015: assigns/propagates request_id. Placed this early so
    # it's available for as much of the request/response cycle (and as
    # many other middlewares' own log lines) as possible.
    "core.middleware.RequestIdMiddleware",
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
    new_path = f"/{db_index}"
    return urlunsplit(
        (parts.scheme, parts.netloc, new_path, parts.query, parts.fragment)
    )


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
    # Part P-018: architecture Section 10 — login endpoint needs strict,
    # dedicated throttling against brute force. This "login" scope is
    # applied ONLY to accounts.views.LoginView via its own
    # throttle_classes (accounts/throttles.py) — it deliberately is NOT
    # added to DEFAULT_THROTTLE_CLASSES, so no other endpoint is
    # affected. General API-wide throttling (all other endpoints) is a
    # separate, later, Phase-wide concern per this part's own scope note.
    "DEFAULT_THROTTLE_RATES": {
        "login": "5/min",
    },
}

# ---------------------------------------------------------------------------
# Simple JWT — Part P-018. Architecture Section 14: refresh-token
# ROTATION with reuse detection (a rotated-away refresh token being
# reused is the theft signal). ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_
# ROTATION together give exactly that: every successful /auth/refresh/
# call issues a brand-new refresh token AND blacklists the one that was
# just used, so presenting that same old token again fails.
#
# Lifetimes: read from env (JWT_ACCESS_TTL_MINUTES / JWT_REFRESH_TTL_DAYS
# — both already reserved in .env.example since Part P-003). Access
# defaults to 15 minutes; refresh defaults to 14 days, the midpoint of
# architecture Section 14's specified 7-30 day range — a reasonable
# default, not the only valid choice, so it's env-overridable rather
# than hardcoded.
# ---------------------------------------------------------------------------
from datetime import timedelta  # noqa: E402

JWT_ACCESS_TTL_MINUTES = env.int("JWT_ACCESS_TTL_MINUTES", default=15)
JWT_REFRESH_TTL_DAYS = env.int("JWT_REFRESH_TTL_DAYS", default=14)

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=JWT_ACCESS_TTL_MINUTES),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=JWT_REFRESH_TTL_DAYS),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}


# ---------------------------------------------------------------------------
# Logging — Part P-015. Every log line is JSON, and every line emitted
# while a request is in flight carries that request's request_id (set
# by core.middleware.RequestIdMiddleware, injected into the record by
# core.logging_utils.RequestIdFilter). Outside of a request (a Celery
# task, a management command) request_id falls back to "no-request"
# rather than crashing — see RequestIdFilter's docstring.
# LOG_LEVEL is read from env, not hardcoded, so it can be turned up in
# a specific environment without a code change.
# ---------------------------------------------------------------------------
LOG_LEVEL = env("LOG_LEVEL", default="INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {
            "()": "core.logging_utils.RequestIdFilter",
        },
    },
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_id"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        # Django's own request/error logging (500s, security warnings,
        # etc.) — routed through the same JSON console handler instead
        # of Django's default plain-text/mail-admins config, so it's
        # request_id-tagged and machine-parseable like everything else.
        "django": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
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
