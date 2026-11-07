"""
Tests for Task P2.7a - scrubbing of password reset secrets from Sentry events
(config/sentry.py).

Pure unit tests: events are plain dicts run through the configured
EventScrubber and before_send hook. sentry_sdk.init is never called with a
real DSN and nothing is ever sent. All data is fake.
"""

import json
from unittest import mock

from sentry_sdk.utils import AnnotatedValue

from config import sentry as sentry_config

SECRETS = {
    "otp": "482915",
    "reset_token": "T" * 43,
    "new_password": "N3w-Str0ng-Uncommon-Pass!",
    "password_confirm": "C0nf1rm-Str0ng-Uncommon-Pass!",
    "refresh": "refresh.jwt.secret-value",
    "access": "access.jwt.secret-value",
    "email": "victim@example.com",
}

TASK_NAME = "accounts.send_password_reset_otp"


def _marker():
    return AnnotatedValue.substituted_because_contains_sensitive_data().value


def _plain(value):
    # The SDK scrubber stores an AnnotatedValue; before_send stores a str.
    return getattr(value, "value", value)


def _dump(event):
    return json.dumps(
        event,
        default=lambda o: o.value if isinstance(o, AnnotatedValue) else repr(o),
    )


def _event(task_name=TASK_NAME):
    return {
        "request": {
            "url": "http://testserver/api/v1/auth/password-reset/confirm/",
            "method": "POST",
            "data": dict(SECRETS),
        },
        "extra": {
            **SECRETS,
            "celery-job": {
                "task_name": task_name,
                "args": [SECRETS["email"]],
                "kwargs": {"email": SECRETS["email"]},
            },
        },
        "contexts": {"custom": {**SECRETS, "nested": [dict(SECRETS)]}},
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {
                                "function": "post",
                                "vars": {**SECRETS, "data": dict(SECRETS)},
                            }
                        ]
                    }
                }
            ]
        },
    }


def _process(event):
    sentry_config._EVENT_SCRUBBER.scrub_event(event)
    return sentry_config._before_send(event, {})


def test_request_body_values_are_replaced_by_the_filtered_marker():
    event = _process(_event())

    for key in SECRETS:
        assert _plain(event["request"]["data"][key]) == _marker()
    # Non-sensitive request fields are untouched.
    assert event["request"]["method"] == "POST"


def test_extra_and_nested_frame_variables_are_scrubbed():
    event = _process(_event())

    for key in SECRETS:
        assert _plain(event["extra"][key]) == _marker()
    frame_vars = event["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
    for key in SECRETS:
        assert _plain(frame_vars[key]) == _marker()
        # recursive=True reaches the dict held in a local variable.
        assert _plain(frame_vars["data"][key]) == _marker()


def test_contexts_are_scrubbed_at_any_depth_by_before_send():
    event = sentry_config._before_send(_event(), {})

    custom = event["contexts"]["custom"]
    for key in SECRETS:
        assert custom[key] == sentry_config._FILTERED
        assert custom["nested"][0][key] == sentry_config._FILTERED


def test_no_secret_value_survives_anywhere_in_the_event():
    dumped = _dump(_process(_event()))

    for value in SECRETS.values():
        assert value not in dumped


def test_celery_args_and_kwargs_of_the_reset_task_are_blanked():
    # Positional args have no key, so the scrubber cannot see them.
    event = _event()
    sentry_config._EVENT_SCRUBBER.scrub_event(event)
    assert event["extra"]["celery-job"]["args"] == [SECRETS["email"]]

    event = sentry_config._before_send(event, {})

    job = event["extra"]["celery-job"]
    assert job["args"] == [sentry_config._FILTERED]
    assert job["kwargs"] == {"email": sentry_config._FILTERED}
    assert job["task_name"] == TASK_NAME
    assert SECRETS["email"] not in _dump(event)


def test_celery_task_is_also_recognised_by_the_transaction_name():
    event = _event(task_name="unexpected-key-shape")
    event["transaction"] = TASK_NAME

    event = sentry_config._before_send(event, {})

    assert event["extra"]["celery-job"]["args"] == [sentry_config._FILTERED]


def test_other_celery_tasks_keep_their_args():
    event = _event(task_name="reports.some_other_task")
    event["extra"]["celery-job"]["args"] = [42, "keep-me"]

    event = sentry_config._before_send(event, {})

    assert event["extra"]["celery-job"]["args"] == [42, "keep-me"]


def test_before_send_never_drops_the_event_and_tolerates_odd_shapes():
    for event in (
        {},
        {"extra": None},
        {"extra": {"celery-job": "not-a-dict"}},
        {"contexts": None},
        {"extra": {"celery-job": {"task_name": TASK_NAME, "args": None}}},
    ):
        assert sentry_config._before_send(event, {}) is event


def test_default_denylist_is_extended_not_replaced_or_mutated():
    denylist = sentry_config._EVENT_SCRUBBER.denylist

    for key in sentry_config._EXTRA_DENYLIST:
        assert key in denylist
    # Defaults are still active.
    assert "password" in denylist
    assert "authorization" in denylist
    # The SDK's own module-level list was not modified.
    assert "new_password" not in sentry_config.DEFAULT_DENYLIST


class TestInitSentry:
    def test_empty_dsn_disables_sentry_without_calling_init(self):
        with mock.patch.object(sentry_config.sentry_sdk, "init") as init:
            assert sentry_config.init_sentry("", "staging") is False
            assert sentry_config.init_sentry(None, "prod") is False

        init.assert_not_called()

    def test_init_passes_the_scrubber_and_before_send_and_keeps_pii_off(self):
        with mock.patch.object(sentry_config.sentry_sdk, "init") as init:
            assert (
                sentry_config.init_sentry("https://k@example.invalid/1", "prod") is True
            )

        init.assert_called_once()
        kwargs = init.call_args.kwargs
        assert kwargs["dsn"] == "https://k@example.invalid/1"
        assert kwargs["environment"] == "prod"
        assert kwargs["send_default_pii"] is False
        assert kwargs["traces_sample_rate"] == 0.0
        assert kwargs["event_scrubber"] is sentry_config._EVENT_SCRUBBER
        assert kwargs["before_send"] is sentry_config._before_send
