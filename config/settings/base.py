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

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
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
# Architecture rule: no local-disk media storage, even as a dev placeholder.
# Media/file uploads are out of scope for this part entirely; the setting
# is deliberately absent rather than pointed at local disk.
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
