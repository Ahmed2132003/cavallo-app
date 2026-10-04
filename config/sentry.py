"""
Sentry initialization (Part P-105).

This is the ONLY place sentry_sdk.init() is called. It is invoked from
config/settings/staging.py and config/settings/prod.py, never from
base.py or dev.py, so local development and the pytest run (which
inherits dev.py) can never send events, even if a developer's .env
contains a real SENTRY_DSN.

sentry-sdk auto-enables its Django, Celery and logging integrations
when those packages are installed, so unhandled exceptions in Celery
tasks and logger.error() calls are captured without extra wiring here.
Exceptions that DRF turns into a response are NOT seen by the Django
integration; core/exceptions.py captures those explicitly (P-105 STEP 2).
"""

import sentry_sdk


def init_sentry(dsn, environment):
    """
    Initialize sentry_sdk for one deployed environment.

    Returns True if Sentry was initialized, False if `dsn` is empty (the
    documented "Sentry disabled" state - no network calls, no error).
    The DSN is never logged or printed.
    """
    if not dsn:
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=0.0,
        send_default_pii=False,
    )
    return True
