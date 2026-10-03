# =====================================================================
# P-096 / STEP 3 of 3  -  IDOR + permission sweep:
#   chat, notifications, devices, analytics, monetization, payments
#
# Run from: D:\Cavallo\scd-backend   (Windows PowerShell)
# Creates 6 NEW test files (no existing file is modified, no production code):
#   chat\test_permission_sweep.py
#   notifications\tests\test_permission_sweep.py
#   devices\tests\test_permission_sweep.py
#   analytics\tests\test_permission_sweep.py
#   monetization\tests\test_permission_sweep.py
#   payments\tests\test_permission_sweep.py
# Requires step 2 (core\tests\sweep_factories.py) and step 1
# (core\tests\sweep_helpers.py) to already exist.
# =====================================================================

$ErrorActionPreference = "Stop"
$root = (Get-Location).Path

if (-not (Test-Path (Join-Path $root "manage.py"))) {
    throw "manage.py not found. cd D:\Cavallo\scd-backend first."
}
foreach ($required in @("core\tests\sweep_helpers.py", "core\tests\sweep_factories.py")) {
    if (-not (Test-Path (Join-Path $root $required))) {
        throw "$required is missing - run p096_step1.ps1 / p096_step2.ps1 first."
    }
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-RepoFile([string]$Relative, [string]$Content) {
    $full = Join-Path $root $Relative
    $dir = Split-Path $full -Parent
    if (-not (Test-Path $dir)) { throw "Missing directory: $dir" }
    # Repo uses CRLF line endings: normalise to CRLF.
    $text = ($Content -replace "`r`n", "`n") -replace "`n", "`r`n"
    $text = $text.TrimEnd() + "`r`n"
    if (Test-Path $full) { Write-Host "OVERWRITE  $Relative" -ForegroundColor Yellow }
    else { Write-Host "CREATE     $Relative" -ForegroundColor Green }
    [System.IO.File]::WriteAllText($full, $text, $utf8NoBom)
}

# ---------------------------------------------------------------------
# 1) chat/test_permission_sweep.py
# ---------------------------------------------------------------------
$chatSweep = @'
"""
P-096 (step 3) permission / IDOR sweep for the chat app's HTTP routes.

Routes (all under /api/v1/conversations/): start (POST), list (GET),
{id}/messages/ (POST send; GET history; GET ?since=), and
users/{id}/presence/ (GET).

The WebSocket side is NOT repeated here: chat/test_consumers.py already
proves the equivalent object-level rules (close code 4001 for a missing /
invalid token, 4003 for an authenticated non-participant) against the
real ASGI application.

Category 1: every route -> 401 envelope (no token and garbage token);
            no Conversation or Message row is created.
Category 2: an authenticated NON-participant gets 403 (envelope) on send,
            history and fetch-since, makes no change, and never sees
            message content; unknown conversation -> 404 envelope; the
            conversation list only ever shows the caller's own
            conversations; a spoofed `sender` / `conversation` / `status`
            in the body is ignored; a third user starting a conversation
            with A can never become a participant of A's existing
            conversation with B; there is no edit/delete route.
Category 3: no capability-gated route exists in this app (chat is
            any-to-any by confirmed decision).

OPEN FINDING S-3 (characterised, NOT changed here): ConversationStartView
builds its 400/404 bodies by hand as {"detail": "..."} instead of raising
DRF exceptions, so those responses do NOT carry the P-012 error envelope
({"error": {"code", "message", "fields"}}). Statuses are right and no
data leaks; only the body shape differs. Fixing it would change what the
Flutter client already parses, so it needs a deliberate decision. The
last tests pin today's shape.

ACCEPTED DESIGN D-2 (characterised): presence is readable by ANY
authenticated user for any existing user id (chat/views.py documents the
decision and says to revisit it). The response is only user_id +
is_online.
"""

from types import SimpleNamespace

import pytest

from chat.models import Conversation, ConversationParticipant, Message
from core.tests.sweep_factories import client_for, garbage_token_client, make_user
from core.tests.sweep_helpers import (
    assert_error_envelope,
    assert_forbidden,
    assert_not_found,
    assert_unauthenticated,
)

pytestmark = pytest.mark.django_db

BASE = "/api/v1/conversations/"
SECRET_TEXT = "private words nobody else may read"


def _conversation(*users):
    conversation = Conversation.objects.create()
    for user in users:
        ConversationParticipant.objects.create(conversation=conversation, user=user)
    return conversation


@pytest.fixture
def world():
    alice, bob, outsider = make_user(), make_user(), make_user()
    conversation = _conversation(alice, bob)
    message = Message.objects.create(
        conversation=conversation, sender=alice, text=SECRET_TEXT
    )
    return SimpleNamespace(
        alice=alice,
        bob=bob,
        outsider=outsider,
        conversation=conversation,
        message=message,
    )


def _messages_url(conversation_id):
    return f"{BASE}{conversation_id}/messages/"


def _requests(w):
    messages = _messages_url(w.conversation.pk)
    return {
        "start": ("post", f"{BASE}start/", {"recipient_id": w.bob.pk}),
        "list": ("get", BASE, None),
        "history": ("get", messages, None),
        "since": ("get", f"{messages}?since=0", None),
        "send": ("post", messages, {"text": "unauthenticated attempt"}),
        "presence": ("get", f"{BASE}users/{w.bob.pk}/presence/", None),
    }


LABELS = ["start", "list", "history", "since", "send", "presence"]


def _send(client, method, url, payload):
    if method == "get":
        return client.get(url)
    return client.post(url, payload or {}, format="json")


def _rows():
    return Conversation.objects.count(), Message.objects.count()


@pytest.mark.parametrize("client_factory", [client_for, garbage_token_client])
@pytest.mark.parametrize("label", LABELS)
def test_unauthenticated_gets_401_and_changes_nothing(world, label, client_factory):
    before = _rows()
    method, url, payload = _requests(world)[label]

    response = _send(client_factory(), method, url, payload)

    assert_unauthenticated(response)
    assert _rows() == before


def test_participant_positive_control_can_read_and_send(world):
    client = client_for(world.bob)

    history = client.get(_messages_url(world.conversation.pk))
    sent = client.post(
        _messages_url(world.conversation.pk), {"text": "hi alice"}, format="json"
    )

    assert history.status_code == 200
    assert SECRET_TEXT in history.content.decode()
    assert sent.status_code == 201


@pytest.mark.parametrize("label", ["history", "since", "send"])
def test_non_participant_gets_403_envelope_sees_nothing_and_changes_nothing(
    world, label
):
    before = _rows()
    method, url, payload = _requests(world)[label]

    response = _send(client_for(world.outsider), method, url, payload)

    assert_forbidden(response)
    assert SECRET_TEXT not in response.content.decode()
    assert _rows() == before
    assert not Message.objects.filter(sender=world.outsider).exists()
    assert not ConversationParticipant.objects.filter(
        conversation=world.conversation, user=world.outsider
    ).exists()


@pytest.mark.parametrize("label", ["history", "since", "send"])
def test_unknown_conversation_is_404_with_envelope_and_changes_nothing(world, label):
    before = _rows()
    method, url, payload = _requests(world)[label]
    url = url.replace(f"/{world.conversation.pk}/", "/999999/")

    response = _send(client_for(world.alice), method, url, payload)

    assert_not_found(response)
    assert _rows() == before


def test_conversation_list_only_contains_the_callers_own_conversations(world):
    other_a, other_b = make_user(), make_user()
    foreign = _conversation(other_a, other_b)

    def listed_ids(user):
        # ConversationListView is not paginated: the body is a plain list.
        response = client_for(user).get(BASE)
        assert response.status_code == 200
        return [item["id"] for item in response.json()]

    assert listed_ids(world.alice) == [world.conversation.pk]
    assert listed_ids(world.outsider) == []
    assert listed_ids(other_a) == [foreign.pk]


def test_spoofed_sender_conversation_and_status_in_the_body_are_ignored(world):
    other = _conversation(make_user(), make_user())

    response = client_for(world.alice).post(
        _messages_url(world.conversation.pk),
        {
            "text": "spoof attempt",
            "sender": world.bob.pk,
            "conversation": other.pk,
            "status": "read",
        },
        format="json",
    )

    assert response.status_code == 201
    message = Message.objects.get(text="spoof attempt")
    assert message.sender_id == world.alice.pk
    assert message.conversation_id == world.conversation.pk
    assert message.status == Message.Status.SENT
    assert Message.objects.filter(conversation=other).count() == 0


def test_third_user_starting_a_chat_never_joins_an_existing_conversation(world):
    response = client_for(world.outsider).post(
        f"{BASE}start/", {"recipient_id": world.alice.pk}, format="json"
    )

    assert response.status_code == 201
    new_id = response.json()["id"]
    assert new_id != world.conversation.pk
    participants = set(
        ConversationParticipant.objects.filter(
            conversation=world.conversation
        ).values_list("user_id", flat=True)
    )
    assert participants == {world.alice.pk, world.bob.pk}
    assert not Message.objects.filter(conversation_id=new_id, text=SECRET_TEXT).exists()


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_messages_collection_has_no_edit_or_delete_handler(world, method):
    response = getattr(client_for(world.alice), method)(
        _messages_url(world.conversation.pk), {"text": "edited"}, format="json"
    )

    assert_error_envelope(response, 405, "METHOD_NOT_ALLOWED")
    world.message.refresh_from_db()
    assert world.message.text == SECRET_TEXT
    assert Message.objects.count() == 1


@pytest.mark.parametrize("actor", ["participant", "outsider"])
@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_no_message_or_conversation_detail_route_exists(world, actor, method):
    user = world.alice if actor == "participant" else world.outsider
    client = client_for(user)

    message_response = getattr(client, method)(
        f"{_messages_url(world.conversation.pk)}{world.message.pk}/"
    )
    conversation_response = getattr(client, method)(f"{BASE}{world.conversation.pk}/")

    assert message_response.status_code == 404
    assert conversation_response.status_code == 404
    assert Message.objects.filter(pk=world.message.pk, text=SECRET_TEXT).exists()
    assert Conversation.objects.filter(pk=world.conversation.pk).exists()


def test_conversation_list_route_has_no_write_handler(world):
    before = _rows()

    response = client_for(world.alice).post(BASE, {}, format="json")

    assert_error_envelope(response, 405, "METHOD_NOT_ALLOWED")
    assert _rows() == before


def test_presence_of_an_unknown_user_is_404_with_envelope(world):
    response = client_for(world.alice).get(f"{BASE}users/999999/presence/")

    assert_not_found(response)


def test_design_d2_presence_is_readable_by_any_authenticated_user(world):
    """Characterisation of accepted design D-2 - see module docstring."""
    response = client_for(world.outsider).get(f"{BASE}users/{world.alice.pk}/presence/")

    assert response.status_code == 200
    assert response.json() == {"user_id": world.alice.pk, "is_online": False}


def test_finding_s3_start_errors_use_detail_not_the_p012_envelope(world):
    client = client_for(world.alice)
    start = f"{BASE}start/"
    before = _rows()

    unknown = client.post(start, {"recipient_id": 999999}, format="json")
    yourself = client.post(start, {"recipient_id": world.alice.pk}, format="json")
    neither = client.post(start, {}, format="json")
    both = client.post(
        start, {"recipient_id": world.bob.pk, "business_id": 1}, format="json"
    )

    assert unknown.status_code == 404
    assert yourself.status_code == 400
    assert neither.status_code == 400
    assert both.status_code == 400
    for response in (unknown, yourself, neither, both):
        assert list(response.json().keys()) == ["detail"]
    assert _rows() == before
'@
Write-RepoFile "chat\test_permission_sweep.py" $chatSweep

# ---------------------------------------------------------------------
# 2) notifications/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$notificationsSweep = @'
"""
P-096 (step 3) permission / IDOR sweep for the notifications app.

Routes: GET /notifications/ (own list), PATCH /notifications/{id}/read/
(mark ONE OWN notification read), GET + PATCH /notifications/preferences/
(own toggles). There is no create, delete or detail route.

Category 1: every route -> 401 envelope (no token and garbage token);
            nothing changes.
Category 2: user B can never read, mark read, or change user A's
            notification or preferences (the object is a 404 for B, and
            A's row is re-read from the DB to prove it is untouched);
            a spoofed `user` / `recipient` in the body is ignored; the
            mark-read endpoint ignores its body entirely.
Category 3: no capability-gated route exists in this app (notifications
            are only ever created by notifications/tasks.py).
"""

import pytest

from core.tests.sweep_factories import client_for, garbage_token_client, make_user
from core.tests.sweep_helpers import assert_not_found, assert_unauthenticated
from notifications.models import Notification, NotificationPreference

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/notifications/"
PREFS_URL = "/api/v1/notifications/preferences/"


def _read_url(pk):
    return f"/api/v1/notifications/{pk}/read/"


def _notify(user, title="hello"):
    return Notification.objects.create(
        recipient=user,
        notification_type=Notification.NotificationType.NEW_FOLLOWER,
        title=title,
        body="body text",
    )


def _prefs(user):
    return NotificationPreference.objects.get(user=user)


@pytest.mark.parametrize("client_factory", [client_for, garbage_token_client])
@pytest.mark.parametrize(
    "method,url,payload",
    [
        ("get", LIST_URL, None),
        ("get", PREFS_URL, None),
        ("patch", PREFS_URL, {"chat_notifications_enabled": False}),
        ("patch", "READ", {}),
    ],
    ids=["list", "prefs-get", "prefs-patch", "mark-read"],
)
def test_unauthenticated_gets_401_and_changes_nothing(
    method, url, payload, client_factory
):
    owner = make_user()
    notification = _notify(owner)
    if url == "READ":
        url = _read_url(notification.pk)

    response = getattr(client_factory(), method)(url, payload or {}, format="json")

    assert_unauthenticated(response)
    notification.refresh_from_db()
    assert notification.is_read is False
    assert _prefs(owner).chat_notifications_enabled is True


def test_list_only_ever_contains_the_callers_own_notifications():
    me, other = make_user(), make_user()
    mine = _notify(me, "mine")
    theirs = _notify(other, "theirs")

    mine_ids = [item["id"] for item in client_for(me).get(LIST_URL).json()["results"]]
    their_ids = [
        item["id"] for item in client_for(other).get(LIST_URL).json()["results"]
    ]

    assert mine_ids == [mine.pk]
    assert their_ids == [theirs.pk]


def test_other_users_mark_read_is_404_and_leaves_the_notification_unread():
    owner, attacker = make_user(), make_user()
    notification = _notify(owner)

    response = client_for(attacker).patch(_read_url(notification.pk), {}, format="json")

    assert_not_found(response)
    notification.refresh_from_db()
    assert notification.is_read is False


def test_unknown_notification_is_404_with_envelope():
    response = client_for(make_user()).patch(_read_url(999999), {}, format="json")

    assert_not_found(response)


def test_mark_read_ignores_the_body_and_changes_nothing_else():
    owner, victim = make_user(), make_user()
    notification = _notify(owner, "original title")

    response = client_for(owner).patch(
        _read_url(notification.pk),
        {
            "is_read": False,
            "title": "hacked",
            "recipient": victim.pk,
            "target_id": 1,
        },
        format="json",
    )

    assert response.status_code == 200
    notification.refresh_from_db()
    assert notification.is_read is True
    assert notification.title == "original title"
    assert notification.recipient_id == owner.pk
    assert notification.target_id is None


def test_other_users_preferences_patch_never_touches_my_row():
    me, other = make_user(), make_user()

    response = client_for(other).patch(
        PREFS_URL,
        {"chat_notifications_enabled": False, "user": me.pk, "id": _prefs(me).pk},
        format="json",
    )

    assert response.status_code == 200
    assert _prefs(me).chat_notifications_enabled is True
    assert _prefs(other).chat_notifications_enabled is False
    assert NotificationPreference.objects.count() == 2


def test_preferences_get_always_returns_the_callers_own_row():
    me, other = make_user(), make_user()
    NotificationPreference.objects.filter(user=other).update(
        social_notifications_enabled=False
    )

    mine = client_for(me).get(PREFS_URL).json()
    theirs = client_for(other).get(PREFS_URL).json()

    assert mine["social_notifications_enabled"] is True
    assert theirs["social_notifications_enabled"] is False


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_no_create_or_modify_handler_exists_on_the_list_route(method):
    me = make_user()
    notification = _notify(me)

    response = getattr(client_for(me), method)(LIST_URL, {}, format="json")

    assert response.status_code == 405
    assert Notification.objects.count() == 1
    notification.refresh_from_db()
    assert notification.is_read is False


@pytest.mark.parametrize("method", ["get", "post", "put", "delete"])
def test_mark_read_route_only_accepts_patch(method):
    me = make_user()
    notification = _notify(me)

    response = getattr(client_for(me), method)(_read_url(notification.pk))

    assert response.status_code == 405
    notification.refresh_from_db()
    assert notification.is_read is False
    assert Notification.objects.filter(pk=notification.pk).exists()


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_no_detail_route_exists_for_a_notification(method):
    me = make_user()
    notification = _notify(me)

    response = getattr(client_for(me), method)(f"{LIST_URL}{notification.pk}/")

    assert response.status_code == 404
    assert Notification.objects.filter(pk=notification.pk).exists()
'@
Write-RepoFile "notifications\tests\test_permission_sweep.py" $notificationsSweep

# ---------------------------------------------------------------------
# 3) devices/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$devicesSweep = @'
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
'@
Write-RepoFile "devices\tests\test_permission_sweep.py" $devicesSweep

# ---------------------------------------------------------------------
# 4) analytics/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$analyticsSweep = @'
"""
P-096 (step 3) permission / IDOR sweep for the analytics app.

The only route is GET /api/v1/analytics/business/{id}/daily/ (owner-only,
read-only, precomputed rows).

Category 1: no token / garbage token -> 401 envelope.
Category 2: any authenticated user who does not OWN the business gets 403
            (another business owner, a customer, an is_staff user, a
            superuser - none of them bypasses ownership) and the body
            carries no stats; ownership is checked BEFORE query
            validation, so a non-owner with a bad query still gets 403,
            not 400; unknown id -> 404 envelope; the route is read-only
            (write methods are 405 and change no row).
Category 3: no capability-gated route exists in this app; the staff /
            superuser cases above prove that elevated Django flags do not
            grant access to another business's analytics.
"""

from datetime import date

import pytest

from analytics.models import BusinessDailyStats
from core.tests.sweep_factories import (
    client_for,
    garbage_token_client,
    make_business,
    make_user,
)
from core.tests.sweep_helpers import (
    assert_forbidden,
    assert_not_found,
    assert_unauthenticated,
)

pytestmark = pytest.mark.django_db


def _url(business_pk):
    return f"/api/v1/analytics/business/{business_pk}/daily/"


def _stats(business, day=date(2026, 1, 2), followers=7):
    return BusinessDailyStats.objects.create(
        business=business, date=day, new_followers=followers
    )


def _user_with(**flags):
    user = make_user()
    for name, value in flags.items():
        setattr(user, name, value)
    user.save()
    return user


@pytest.mark.parametrize("client_factory", [client_for, garbage_token_client])
def test_unauthenticated_gets_401_envelope(client_factory):
    business = make_business()
    _stats(business)

    response = client_factory().get(_url(business.pk))

    assert_unauthenticated(response)


def test_owner_positive_control_sees_their_own_stats():
    business = make_business()
    _stats(business, followers=7)

    response = client_for(business.user).get(_url(business.pk))

    assert response.status_code == 200
    assert [row["new_followers"] for row in response.json()["results"]] == [7]


@pytest.mark.parametrize(
    "actor",
    ["other_business", "customer", "staff", "superuser"],
)
def test_non_owner_gets_403_envelope_and_no_stats_leak(actor):
    business = make_business("Victim Biz")
    _stats(business, followers=424242)
    if actor == "other_business":
        attacker = make_business("Attacker Biz").user
    elif actor == "customer":
        attacker = make_user("customer")
    elif actor == "staff":
        attacker = _user_with(is_staff=True)
    else:
        attacker = _user_with(is_staff=True, is_superuser=True)

    response = client_for(attacker).get(_url(business.pk))

    assert_forbidden(response)
    assert "424242" not in response.content.decode()


def test_ownership_is_checked_before_query_validation():
    business = make_business()
    _stats(business)
    attacker = make_business("Attacker Biz").user

    response = client_for(attacker).get(
        _url(business.pk), {"date_from": "not-a-date", "date_to": "also-bad"}
    )

    assert_forbidden(response)


def test_unknown_business_is_404_with_envelope():
    response = client_for(make_user()).get(_url(999999))

    assert_not_found(response)


@pytest.mark.parametrize("actor", ["owner", "other_user"])
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_route_is_read_only_and_changes_no_row(actor, method):
    business = make_business()
    stats = _stats(business, followers=7)
    user = business.user if actor == "owner" else make_user()

    response = getattr(client_for(user), method)(
        _url(business.pk), {"new_followers": 999}, format="json"
    )

    assert response.status_code == 405
    assert BusinessDailyStats.objects.count() == 1
    stats.refresh_from_db()
    assert stats.new_followers == 7
'@
Write-RepoFile "analytics\tests\test_permission_sweep.py" $analyticsSweep

# ---------------------------------------------------------------------
# 5) monetization/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$monetizationSweep = @'
"""
P-096 (step 3) permission sweep for the monetization app.

The only route is the public, read-only GET /api/v1/monetization/plans/
(authentication_classes = [] on purpose, so the Web Dashboard can show
pricing before login). Activating Featured status is NEVER exposed over
HTTP (monetization.services.activate_subscription is called only by the
payments webhook / reconciliation).

Category 1 (adapted): the route is public by design, so "401" does not
            apply; instead a missing, valid or garbage token all get the
            same 200 with the same body, and the response exposes only
            the five documented Plan fields.
Category 2: no client can create, change or delete a Plan or a
            FeaturedSubscription: write methods are 405 with the P-012
            envelope for anonymous AND signed-in users, no detail route
            exists, and no plausible activation route exists (a business
            owner cannot make themselves Featured over HTTP).
Category 3: no capability-gated route exists in this app (Plans and
            subscriptions are managed in Django Admin only).
"""

import pytest

from core.tests.sweep_factories import (
    client_for,
    garbage_token_client,
    make_business,
    make_user,
)
from core.tests.sweep_helpers import assert_error_envelope
from monetization.models import FeaturedSubscription, Plan
from payments.tests.factories import make_plan

pytestmark = pytest.mark.django_db

URL = "/api/v1/monetization/plans/"
PLAN_FIELDS = {"id", "name", "duration_days", "price", "currency"}


@pytest.mark.parametrize(
    "client_factory",
    [client_for, garbage_token_client, lambda: client_for(make_user())],
    ids=["anonymous", "garbage-token", "signed-in-customer"],
)
def test_public_list_is_identical_for_every_caller_and_exposes_only_plan_fields(
    client_factory,
):
    make_plan(name="Featured 30", days=30)
    make_plan(name="Featured 7", days=7, price="80.00")

    response = client_factory().get(URL)

    assert response.status_code == 200
    body = response.json()
    assert [plan["duration_days"] for plan in body] == [7, 30]
    for plan in body:
        assert set(plan) == PLAN_FIELDS


@pytest.mark.parametrize("actor", ["anonymous", "business_owner"])
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_plans_cannot_be_created_changed_or_deleted_over_http(actor, method):
    plan = make_plan(name="Original", price="250.00")
    client = client_for() if actor == "anonymous" else client_for(make_business().user)

    response = getattr(client, method)(
        URL, {"name": "Hacked", "price": "0.01", "duration_days": 1}, format="json"
    )

    assert_error_envelope(response, 405, "METHOD_NOT_ALLOWED")
    assert Plan.objects.count() == 1
    plan.refresh_from_db()
    assert plan.name == "Original"
    assert str(plan.price) == "250.00"


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_no_detail_route_exists_for_a_plan(method):
    plan = make_plan()
    client = client_for(make_business().user)

    response = getattr(client, method)(f"{URL}{plan.pk}/")

    assert response.status_code == 404
    assert Plan.objects.filter(pk=plan.pk).exists()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/monetization/",
        "/api/v1/monetization/activate/",
        "/api/v1/monetization/subscribe/",
        "/api/v1/monetization/subscriptions/",
        "/api/v1/monetization/featured/",
    ],
)
def test_a_business_owner_cannot_make_themselves_featured_over_http(path):
    business = make_business()
    plan = make_plan()

    response = client_for(business.user).post(
        path, {"plan_id": plan.pk, "business_id": business.pk}, format="json"
    )

    assert response.status_code in (404, 405)
    assert FeaturedSubscription.objects.count() == 0
    business.refresh_from_db()
    assert business.is_featured is False
'@
Write-RepoFile "monetization\tests\test_permission_sweep.py" $monetizationSweep

# ---------------------------------------------------------------------
# 6) payments/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$paymentsSweep = @'
"""
P-096 (step 3) permission sweep for the payments app.

Routes: POST /api/v1/payments/initiate/ (authenticated Business owner)
and POST /api/v1/payments/webhook/paymob/ (called by the gateway, so
unauthenticated BY NECESSITY and secured by the HMAC signature instead).

Initiate:
  Category 1: no token / garbage token -> 401 envelope, no payment rows.
  Category 2: the buyer is always request.user's own business (covered by
              test_initiate_api); only POST exists - every other method is
              405 with the envelope and creates nothing.
  Category 3: a Customer account (wrong role) -> 403 envelope, no rows.

Webhook (adapted - there is no user to be a "non-owner"):
  Category 1 (adapted): being logged in is neither needed nor sufficient.
              A business owner's own JWT cannot stand in for the
              signature (400, nothing changes, nothing is activated), and
              a garbage Authorization header does not turn a correctly
              signed webhook into a 401, because the view declares no
              authentication classes.
  Category 2 (adapted): only POST exists; GET/PUT/PATCH/DELETE are 405
              and change nothing.
  Category 3: n/a - no capability-gated route; the signature is the only
              gate.

Every "nothing changes" claim is proven by comparing database snapshots,
not just status codes.
"""

import pytest
from rest_framework.test import APIClient

from core.tests.sweep_factories import client_for, garbage_token_client, make_user
from core.tests.sweep_helpers import (
    assert_error_envelope,
    assert_forbidden,
    assert_unauthenticated,
)
from monetization.models import FeaturedSubscription
from payments.models import STATUS_COMPLETED, Subscription, Transaction
from payments.tests.factories import make_business, make_plan
from payments.tests.webhook_helpers import (
    SECRET,
    WEBHOOK_URL,
    build_payload,
    db_snapshot,
    make_pending_payment,
    post_webhook,
    sign,
)

pytestmark = pytest.mark.django_db

INITIATE_URL = "/api/v1/payments/initiate/"


@pytest.fixture(autouse=True)
def _webhook_secret(settings):
    settings.PAYMOB_WEBHOOK_SECRET = SECRET


@pytest.fixture
def fake_gateway(settings):
    # Initiate tests only: the webhook tests must use the real configured
    # gateway so the real HMAC check runs.
    settings.PAYMENT_GATEWAY = "payments.tests.fake_gateway.FakeGateway"


def _payment_rows():
    return Subscription.objects.count(), Transaction.objects.count()


# --------------------------------------------------------------- initiate


@pytest.mark.parametrize("client_factory", [client_for, garbage_token_client])
def test_initiate_unauthenticated_gets_401_and_creates_nothing(
    fake_gateway, client_factory
):
    plan = make_plan()

    response = client_factory().post(INITIATE_URL, {"plan_id": plan.pk}, format="json")

    assert_unauthenticated(response)
    assert _payment_rows() == (0, 0)


def test_initiate_wrong_role_customer_gets_403_envelope_and_creates_nothing(
    fake_gateway,
):
    plan = make_plan()

    response = client_for(make_user("customer")).post(
        INITIATE_URL, {"plan_id": plan.pk}, format="json"
    )

    assert_forbidden(response)
    assert _payment_rows() == (0, 0)
    assert FeaturedSubscription.objects.count() == 0


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_initiate_only_accepts_post(fake_gateway, method):
    owner = make_business("owner").user
    make_plan()

    response = getattr(client_for(owner), method)(INITIATE_URL)

    assert_error_envelope(response, 405, "METHOD_NOT_ALLOWED")
    assert _payment_rows() == (0, 0)


# ---------------------------------------------------------------- webhook


def test_business_owner_jwt_cannot_replace_the_webhook_signature():
    subscription, _txn = make_pending_payment()
    before = db_snapshot()
    owner_client = client_for(subscription.business.user)

    response = post_webhook(owner_client, build_payload(), send_hmac=False)

    assert response.status_code == 400
    assert db_snapshot() == before
    assert FeaturedSubscription.objects.count() == 0
    subscription.business.refresh_from_db()
    assert subscription.business.is_featured is False


def test_garbage_authorization_header_does_not_break_a_signed_webhook():
    subscription, _txn = make_pending_payment()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    payload = build_payload()

    response = post_webhook(client, payload, hmac_value=sign(payload))

    assert response.status_code == 200
    subscription.refresh_from_db()
    assert subscription.status == STATUS_COMPLETED


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_webhook_only_accepts_post_and_changes_nothing(method):
    make_pending_payment()
    before = db_snapshot()

    response = getattr(APIClient(), method)(WEBHOOK_URL)

    assert_error_envelope(response, 405, "METHOD_NOT_ALLOWED")
    assert db_snapshot() == before
'@
Write-RepoFile "payments\tests\test_permission_sweep.py" $paymentsSweep

Write-Host ""
Write-Host "Done: 6 files written. No existing file was modified." -ForegroundColor Cyan
Write-Host "Next: format + run the checks from the STEP 3 message."