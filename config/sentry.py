"""
Sentry initialization (Part P-105, scrubbing hardened in Task P2.7a).

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

Task P2.7a - password reset secrets never leave the process:
  - The SDK's default scrubber only replaces a value when the KEY equals
    a denylist entry exactly ("password", "token", ...). Our field names
    ("new_password", "reset_token", "otp", ...) are not on that list, so
    EventScrubber below extends DEFAULT_DENYLIST with them. recursive=True
    makes it also look inside nested dicts (frame variables such as
    `data = serializer.validated_data`, Celery kwargs).
  - EventScrubber matches dict KEYS only. Celery task args are a plain list
    ("args": ["user@example.com"]) with no keys, and the Celery integration
    attaches them to EVERY event raised inside a task, including the
    logger.error() events our tasks emit when they swallow a failure. So
    _before_send blanks the args/kwargs of the password reset task. It also
    applies the same key denylist to event["contexts"], which EventScrubber
    does not cover. It writes a plain string marker (never an
    AnnotatedValue), so it is safe whether the SDK calls it before or after
    it serializes the event.
"""

import sentry_sdk
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

_FILTERED = "[Filtered]"

# Field names used by the auth / password reset API that the SDK default
# denylist does not know about.
_EXTRA_DENYLIST = [
    "new_password",
    "reset_token",
    "otp",
    "email",
    "password_confirm",
    "refresh",
    "access",
]

# `DEFAULT_DENYLIST + [...]` builds a NEW list: EventScrubber appends its PII
# entries to the list it is given, so the module-level default must not be
# passed in directly. send_default_pii stays False (see init_sentry), so the
# scrubber's default PII denylist (client IP headers etc.) is kept as well.
_EVENT_SCRUBBER = EventScrubber(
    denylist=DEFAULT_DENYLIST + _EXTRA_DENYLIST,
    recursive=True,
)

_CONTEXT_DENYLIST = frozenset(key.lower() for key in DEFAULT_DENYLIST + _EXTRA_DENYLIST)

# Celery tasks whose positional/keyword arguments carry personal data. Matched
# by registered task name (accounts/tasks.py).
_SENSITIVE_CELERY_TASKS = frozenset({"accounts.send_password_reset_otp"})


def _redact_keys(node):
    """Replace the value of every denylisted key, at any depth, in place."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in _CONTEXT_DENYLIST:
                node[key] = _FILTERED
            else:
                _redact_keys(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _redact_keys(item)


def _before_send(event, hint):
    """
    Last line of defence run on every error event (never on transactions).
    Returns the same event, modified in place; it never drops an event.
    """
    contexts = event.get("contexts")
    if contexts:
        _redact_keys(contexts)

    job = (event.get("extra") or {}).get("celery-job")
    if isinstance(job, dict) and (
        {job.get("task_name"), event.get("transaction")} & _SENSITIVE_CELERY_TASKS
    ):
        args = job.get("args")
        if isinstance(args, (list, tuple)):
            job["args"] = [_FILTERED] * len(args)
        kwargs = job.get("kwargs")
        if isinstance(kwargs, dict):
            job["kwargs"] = {key: _FILTERED for key in kwargs}

    return event


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
        event_scrubber=_EVENT_SCRUBBER,
        before_send=_before_send,
    )
    return True
