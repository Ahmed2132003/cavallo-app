"""
Tests for the Sentry alert added to check_moderation_sla (Part P-105, STEP 3).

Architecture Section 24 names the moderation backlog the single most
important alert in the system, so these tests use a REAL sentry_sdk client
with an in-memory transport (no mocks on the capture path) and assert on
the event that would actually be sent: level, message, fingerprint, tag
and context. No network, and the client is removed after every test.
"""

import logging
from datetime import timedelta
from unittest import mock

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from moderation.models import ModerationQueue
from moderation.tasks import (
    FAST_PATH_SLA_MINUTES,
    NORMAL_SLA_HOURS,
    SENTRY_MAX_ITEM_IDS,
    check_moderation_sla,
)
from moderation.tests.test_tasks import _backdated_queue_item

pytestmark = pytest.mark.django_db

FAST = ModerationQueue.Priority.FAST_PATH
NORMAL = ModerationQueue.Priority.NORMAL
FAST_BREACH_AGE = timedelta(minutes=FAST_PATH_SLA_MINUTES + 1)
NORMAL_BREACH_AGE = timedelta(hours=NORMAL_SLA_HOURS + 1)


class _ListTransport(Transport):
    """In-memory transport: collects events instead of sending them."""

    def __init__(self, sink):
        super().__init__()
        self.sink = sink

    def capture_envelope(self, envelope):
        event = envelope.get_event()
        if event is not None:
            self.sink.append(event)


@pytest.fixture
def sentry_events():
    events = []
    sentry_sdk.init(
        dsn="https://publickey@o0.ingest.example.invalid/1",
        transport=_ListTransport(events),
        traces_sample_rate=0.0,
    )
    try:
        yield events
    finally:
        sentry_sdk.flush()
        sentry_sdk.get_global_scope().set_client(None)


def _run(events):
    result = check_moderation_sla()
    sentry_sdk.flush()
    return result, events


def test_fast_path_breach_sends_one_error_event(sentry_events):
    item = _backdated_queue_item(priority=FAST, age=FAST_BREACH_AGE)

    result, events = _run(sentry_events)

    assert result["fast_path_breaches"] == 1
    assert len(events) == 1
    event = events[0]
    assert event["level"] == "error"
    assert event["message"].startswith("Moderation SLA breach: 1 fast_path item(s)")
    assert event["fingerprint"] == ["moderation-sla-breach", "fast_path"]
    assert event["tags"]["moderation_sla_priority"] == "fast_path"
    context = event["contexts"]["moderation_sla"]
    assert context["breach_count"] == 1
    assert context["queue_item_ids"] == [item.pk]
    assert context["threshold"] == f"{FAST_PATH_SLA_MINUTES} min"
    assert context["oldest_age_seconds"] >= FAST_BREACH_AGE.total_seconds()


def test_normal_breach_sends_one_warning_event(sentry_events):
    item = _backdated_queue_item(priority=NORMAL, age=NORMAL_BREACH_AGE)

    result, events = _run(sentry_events)

    assert result["normal_breaches"] == 1
    assert len(events) == 1
    event = events[0]
    assert event["level"] == "warning"
    assert event["message"].startswith("Moderation SLA warning: 1 normal item(s)")
    assert event["fingerprint"] == ["moderation-sla-breach", "normal"]
    assert event["contexts"]["moderation_sla"]["queue_item_ids"] == [item.pk]


def test_many_breaches_in_one_tier_are_one_aggregated_event(sentry_events):
    oldest = _backdated_queue_item(priority=FAST, age=timedelta(hours=3))
    _backdated_queue_item(priority=FAST, age=FAST_BREACH_AGE)
    _backdated_queue_item(priority=FAST, age=timedelta(minutes=45))

    result, events = _run(sentry_events)

    assert result["fast_path_breaches"] == 3
    assert len(events) == 1
    context = events[0]["contexts"]["moderation_sla"]
    assert context["breach_count"] == 3
    assert context["oldest_age_seconds"] >= timedelta(hours=3).total_seconds()
    assert oldest.pk in context["queue_item_ids"]
    assert "oldest 180 min" in events[0]["message"]


def test_both_tiers_send_two_events_fast_path_first(sentry_events):
    _backdated_queue_item(priority=NORMAL, age=NORMAL_BREACH_AGE)
    _backdated_queue_item(priority=FAST, age=FAST_BREACH_AGE)

    _, events = _run(sentry_events)

    assert [e["level"] for e in events] == ["error", "warning"]
    assert events[0]["fingerprint"] != events[1]["fingerprint"]


def test_item_ids_are_capped_but_count_is_exact(sentry_events):
    total = SENTRY_MAX_ITEM_IDS + 5
    for _ in range(total):
        _backdated_queue_item(priority=FAST, age=FAST_BREACH_AGE)

    _, events = _run(sentry_events)

    assert len(events) == 1
    context = events[0]["contexts"]["moderation_sla"]
    assert context["breach_count"] == total
    assert len(context["queue_item_ids"]) == SENTRY_MAX_ITEM_IDS


def test_no_breach_sends_nothing(sentry_events):
    _backdated_queue_item(
        priority=FAST, age=timedelta(minutes=FAST_PATH_SLA_MINUTES - 5)
    )
    _backdated_queue_item(priority=NORMAL, age=timedelta(hours=NORMAL_SLA_HOURS - 1))

    result, events = _run(sentry_events)

    assert result == {"fast_path_breaches": 0, "normal_breaches": 0}
    assert events == []


def test_already_decided_items_send_nothing(sentry_events):
    item = _backdated_queue_item(priority=FAST, age=FAST_BREACH_AGE)
    ModerationQueue.objects.filter(pk=item.pk).update(
        status=ModerationQueue.Status.APPROVED
    )

    _, events = _run(sentry_events)

    assert events == []


def test_each_run_sends_a_fresh_event_with_the_same_fingerprint(sentry_events):
    _backdated_queue_item(priority=FAST, age=FAST_BREACH_AGE)

    check_moderation_sla()
    check_moderation_sla()
    sentry_sdk.flush()

    assert len(sentry_events) == 2
    assert sentry_events[0]["fingerprint"] == sentry_events[1]["fingerprint"]


def test_sentry_failure_does_not_break_the_task_or_the_log_lines(caplog):
    _backdated_queue_item(priority=FAST, age=FAST_BREACH_AGE)

    with mock.patch(
        "sentry_sdk.capture_message", side_effect=RuntimeError("sentry is down")
    ):
        with caplog.at_level(logging.WARNING, logger="moderation.tasks"):
            result = check_moderation_sla()

    assert result == {"fast_path_breaches": 1, "normal_breaches": 0}
    messages = [r.message for r in caplog.records]
    assert "moderation_sla_breach" in messages
    assert "moderation_sla_alert_failed" in messages


def test_without_sentry_the_task_behaves_exactly_as_in_p039():
    _backdated_queue_item(priority=FAST, age=FAST_BREACH_AGE)
    assert not sentry_sdk.is_initialized()

    assert check_moderation_sla() == {"fast_path_breaches": 1, "normal_breaches": 0}
