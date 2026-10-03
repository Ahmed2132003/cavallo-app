"""
P-096 (step 2) permission sweep for the reports app.

The only route is POST /api/v1/reports/ (create-only; no read, edit or
delete route exists, so a reporter's identity and a report's status can
never be read or changed through the API). The reports/tests/conftest
`business` fixture and its autouse throttle-cache reset apply here.

Category 1: no token / garbage token -> 401 envelope, no Report row.
Category 2: the reporter is always request.user (a spoofed `reporter`
            or `user` in the body is ignored); no GET/PUT/PATCH/DELETE
            handler exists, and no detail route exists, so an existing
            report cannot be altered.
Category 3: reports have no capability-gated API route (triage is in
            Django Admin, covered by reports/tests/test_admin.py).
"""

import pytest

from core.tests.sweep_factories import (
    client_for,
    garbage_token_client,
    make_post,
    make_user,
)
from core.tests.sweep_helpers import assert_error_envelope, assert_unauthenticated
from reports.models import Report

pytestmark = pytest.mark.django_db

URL = "/api/v1/reports/"


def _payload(post, **extra):
    return {"content_type": "post", "object_id": post.pk, "reason": "spam", **extra}


@pytest.mark.parametrize("client_factory", [client_for, garbage_token_client])
def test_unauthenticated_gets_401_and_creates_no_report(business, client_factory):
    post = make_post(business)

    response = client_factory().post(URL, _payload(post), format="json")

    assert_unauthenticated(response)
    assert Report.objects.count() == 0


def test_spoofed_reporter_in_the_body_is_ignored(business):
    post = make_post(business)
    reporter, victim = make_user(), make_user()

    response = client_for(reporter).post(
        URL, _payload(post, reporter=victim.pk, user=victim.pk), format="json"
    )

    assert response.status_code == 201
    assert Report.objects.get().reporter_id == reporter.pk


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_no_read_or_modify_handler_exists_on_the_collection(business, method):
    post = make_post(business)
    reporter = make_user()
    client = client_for(reporter)
    client.post(URL, _payload(post), format="json")
    report = Report.objects.get()

    response = getattr(client, method)(URL)

    assert_error_envelope(response, 405, "METHOD_NOT_ALLOWED")
    assert Report.objects.count() == 1
    report.refresh_from_db()
    assert report.status == Report.Status.PENDING


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_no_detail_route_exists_for_a_report(business, method):
    post = make_post(business)
    client = client_for(make_user())
    client.post(URL, _payload(post), format="json")
    report = Report.objects.get()

    response = getattr(client, method)(f"{URL}{report.pk}/")

    assert response.status_code == 404
    assert Report.objects.filter(pk=report.pk).exists()
