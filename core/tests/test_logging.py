"""
Tests confirming request_id correlation end-to-end (Part P-015):
RequestIdMiddleware assigns request_id, RequestIdFilter (core/
logging_utils.py) injects it into every log record reaching a handler
it's attached to.
"""

import logging

import pytest
from django.test import Client

from core.logging_utils import RequestIdFilter

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return Client()


def test_request_id_appears_in_log_records_for_a_request(client, caplog):
    # pytest's own capture handler doesn't have RequestIdFilter attached
    # by default (only the real "console" handler in LOGGING does).
    # Attaching the actual filter class here — not a re-implementation
    # of its logic — is what makes this a real test of the filter.
    caplog.handler.addFilter(RequestIdFilter())

    with caplog.at_level(logging.INFO, logger="core.middleware"):
        response = client.get("/health/")

    matching = [r for r in caplog.records if r.message == "request completed"]
    assert matching, 'expected RequestIdMiddleware to log a "request completed" record'
    assert matching[0].request_id == response["X-Request-ID"]


def test_request_id_defaults_to_no_request_outside_request_context(caplog):
    caplog.handler.addFilter(RequestIdFilter())

    with caplog.at_level(logging.INFO, logger="core.tests.test_logging"):
        logging.getLogger(__name__).info("a log line with no request in flight")

    assert caplog.records[-1].request_id == "no-request"
