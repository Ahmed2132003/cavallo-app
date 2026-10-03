"""
P-096 (step 3) permission / IDOR sweep for the devices app.

The only route is POST /api/v1/devices/register/ (an upsert keyed by
the globally-unique FCM token). There is no list, detail or delete
route, so no client can enumerate, read or remove a device row.

Category 1: no token / garbage token -> 401 envelope, no DeviceToken row.
Category 2: the owner is ALWAYS request.user (a spoofed `user` /
            `user_id` in the body is ignored); registering a NEW token
            never touches anyone else's tokens; invalid input creates
            nothing; no read/modify/delete handler or detail route exists.
Category 3: no capability-gated route exists in this app.

ACCEPTED DESIGN D-1 (characterised, NOT changed here): registering a
token that already exists MOVES it to the caller (devices/views.py
documents why: a phone changes hands, or the app is reinstalled). The
safeguard is that an FCM token is an unguessable secret known only to
the device; the last test pins this behaviour so a change is visible.
"""

import pytest

from core.tests.sweep_factories import client_for, garbage_token_client, make_user
from core.tests.sweep_helpers import assert_error_envelope, assert_unauthenticated
from devices.models import DeviceToken

pytestmark = pytest.mark.django_db

URL = "/api/v1/devices/register/"


def _assert_validation_error(response):
    # P-012 shape for a 400: code VALIDATION_ERROR, per-field messages.
    assert response.status_code == 400, response.content[:300]
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR", error
    assert error["fields"], error


TOKEN_A = "sweep-fcm-token-a-" + "x" * 40
TOKEN_B = "sweep-fcm-token-b-" + "y" * 40


def _register(client, token=TOKEN_A, platform="android", **extra):
    return client.post(
        URL, {"token": token, "platform": platform, **extra}, format="json"
    )


@pytest.mark.parametrize("client_factory", [client_for, garbage_token_client])
def test_unauthenticated_gets_401_and_creates_no_device(client_factory):
    response = _register(client_factory())

    assert_unauthenticated(response)
    assert DeviceToken.objects.count() == 0


def test_spoofed_owner_fields_in_the_body_are_ignored():
    attacker, victim = make_user(), make_user()

    response = _register(client_for(attacker), user=victim.pk, user_id=victim.pk)

    assert response.status_code == 201
    device = DeviceToken.objects.get()
    assert device.user_id == attacker.pk
    assert DeviceToken.objects.filter(user=victim).count() == 0


def test_registering_a_new_token_never_touches_another_users_tokens():
    me, other = make_user(), make_user()
    _register(client_for(me), token=TOKEN_A, platform="ios")
    before = list(
        DeviceToken.objects.filter(user=me).values_list("pk", "token", "platform")
    )

    response = _register(client_for(other), token=TOKEN_B, platform="android")

    assert response.status_code == 201
    after = list(
        DeviceToken.objects.filter(user=me).values_list("pk", "token", "platform")
    )
    assert after == before
    assert DeviceToken.objects.get(token=TOKEN_B).user_id == other.pk


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"token": TOKEN_A},
        {"platform": "ios"},
        {"token": TOKEN_A, "platform": "windows-phone"},
        {"token": "   ", "platform": "ios"},
        {"token": "t" * 513, "platform": "ios"},
    ],
    ids=["empty", "no-platform", "no-token", "bad-platform", "blank-token", "too-long"],
)
def test_invalid_input_is_400_with_envelope_and_creates_nothing(body):
    response = client_for(make_user()).post(URL, body, format="json")

    _assert_validation_error(response)
    assert DeviceToken.objects.count() == 0


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_no_read_or_modify_handler_exists_on_the_register_route(method):
    me = make_user()
    _register(client_for(me))
    device = DeviceToken.objects.get()

    response = getattr(client_for(me), method)(URL)

    assert_error_envelope(response, 405, "METHOD_NOT_ALLOWED")
    assert DeviceToken.objects.count() == 1
    device.refresh_from_db()
    assert device.user_id == me.pk


@pytest.mark.parametrize("path", ["/api/v1/devices/", "/api/v1/devices/{pk}/"])
@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_no_list_or_detail_route_exists(path, method):
    me, other = make_user(), make_user()
    _register(client_for(me))
    device = DeviceToken.objects.get()

    response = getattr(client_for(other), method)(path.format(pk=device.pk))

    assert response.status_code == 404
    assert DeviceToken.objects.filter(pk=device.pk, user=me).exists()


def test_design_d1_registering_an_existing_token_moves_it_to_the_caller():
    """Characterisation of accepted design D-1 - see module docstring."""
    first, second = make_user(), make_user()
    _register(client_for(first))

    response = _register(client_for(second), platform="ios")

    assert response.status_code == 200
    device = DeviceToken.objects.get()
    assert device.user_id == second.pk
    assert device.platform == "ios"
