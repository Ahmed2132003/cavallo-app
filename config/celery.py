import os

from celery import Celery
from celery.signals import setup_logging

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("config")

# Read CELERY_* settings from Django's settings.py (see config/settings.py).
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py in every installed app.
app.autodiscover_tasks()


@setup_logging.connect
def configure_celery_logging(*args, **kwargs):
    """
    Discovered during Part P-039: Celery hijacks the root logger by
    default at worker startup, silently discarding Django's LOGGING
    config (P-015's JSON formatter) for every task, not just this one.
    Connecting anything to this signal tells Celery "logging is
    already configured, don't touch it" — so we explicitly re-apply
    Django's exact LOGGING dict here instead, keeping every Celery
    task's logs JSON/structured and consistent with request-driven
    logs.
    """
    from logging.config import dictConfig

    from django.conf import settings

    dictConfig(settings.LOGGING)


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")