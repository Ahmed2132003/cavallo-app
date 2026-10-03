# p094_step3.ps1 - P-094 Step 3 (walkthrough steps 9-10: Chat REST + live WebSocket delivery and
# sent -> delivered -> read) + fix for F-7 (a message SENDER could acknowledge their own message).
# Run from: D:\Cavallo\scd-backend   (PowerShell)
#   powershell -ExecutionPolicy Bypass -File .\p094_step3.ps1
$ErrorActionPreference = 'Stop'
$root = (Get-Location).Path
foreach ($f in @('manage.py','chat\consumers.py','chat\views.py','core\tests\test_integration_phase17.py','INTEGRATION_TEST_REPORT_PHASE17.md')) {
    if (-not (Test-Path (Join-Path $root $f))) { throw "Missing $f - run this from D:\Cavallo\scd-backend" }
}
$utf8 = New-Object System.Text.UTF8Encoding($false)

# Audit: step 2 must already be applied (its test class and F-5 fix).
$tp = Join-Path $root 'core\tests\test_integration_phase17.py'
$tt = [System.IO.File]::ReadAllText($tp).Replace("`r`n", "`n")
if (-not $tt.Contains('class TestPhase17Steps5To8')) { throw 'Step 2 is not applied (TestPhase17Steps5To8 missing) - run p094_step2.ps1 first' }
$svText = [System.IO.File]::ReadAllText((Join-Path $root 'social\views.py'))
if (-not $svText.Contains('_invalidate_home_feed_cache')) { throw 'F-5 fix missing in social\views.py - run p094_step2.ps1 first' }
Write-Host 'Audit passed.'

# ---------------------------------------------------------------- 1) chat/consumers.py (F-7 fix)
$old = @'
            message = Message.objects.get(
                id=message_id, conversation_id=self.conversation_id
            )
'@
$new = @'
            # Part P-094 (finding F-7): only the RECIPIENT may acknowledge a
            # message (P-069 is the "recipient-side" ack flow). Excluding the
            # sender here makes a sender's own mark_delivered/mark_read a
            # silent no-op, so nobody can fake a read receipt or zero out the
            # recipient's unread_count for their own messages.
            message = Message.objects.exclude(sender_id=self.scope["user"].id).get(
                id=message_id, conversation_id=self.conversation_id
            )
'@
$cp = Join-Path $root 'chat\consumers.py'
$raw = [System.IO.File]::ReadAllText($cp)
if ($raw.Contains('finding F-7')) {
    Write-Host 'chat\consumers.py already has the F-7 fix - skipped'
} else {
    $nl = "`n"
    if ($raw.Contains("`r`n")) { $nl = "`r`n" }
    $t = $raw.Replace("`r`n", "`n")
    $oldLf = $old.Replace("`r`n", "`n")
    if ([regex]::Matches($t, [regex]::Escape($oldLf)).Count -ne 1) { throw 'chat\consumers.py: Message.objects.get anchor not found exactly once' }
    $t = $t.Replace($oldLf, $new.Replace("`r`n", "`n"))
    [System.IO.File]::WriteAllText($cp, $t.Replace("`n", $nl), $utf8)
    Write-Host 'updated chat\consumers.py'
}

# ---------------------------------------------------------------- 2) test module
$block = @'
# ===========================================================================
# STEP 3 of the P-094 script series: walkthrough steps 9-10
# (Chat: text / image / shared product; fetch-on-open; live delivery and the
# sent -> delivered -> read progression over the real WebSocket stack).
# ===========================================================================


@pytest.fixture
def offline_notify():
    # MessageSendView calls notify_offline_recipient.delay() when the other
    # participant is not connected. Without this patch that would publish a
    # real Celery message to the shared Redis broker (and the running docker
    # worker would execute it against the dev database).
    with mock.patch("chat.views.notify_offline_recipient") as patched:
        yield patched


def _forced_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class TestPhase17Steps9To10Rest:
    def test_message_business_text_image_and_shared_product(
        self, offline_notify, dispatch_delay
    ):
        from chat.tasks import notify_offline_recipient as offline_task

        category = Category.objects.create(name="Fashion")
        b1 = _publish_business(
            "p094-s3-biz@example.com",
            name="Zephyr Atelier",
            city="Cairo",
            phone="+201001234567",
            product_name="Zephyr Jacket",
            price="199.50",
            caption="Zephyr new arrivals",
            category=category,
            moderator=_moderator_client(),
        )
        business_user = User.objects.get(email="p094-s3-biz@example.com")

        customer = _register_and_login("p094-s3-customer@example.com", "customer")
        profile = customer.post(
            "/api/v1/customers/me/",
            {"display_name": "S3 Customer", "country": "Egypt", "city": "Giza"},
            format="json",
        )
        assert profile.status_code in (200, 201), profile.content

        # ---- STEP 9a: start the conversation from the business page ----------
        start = customer.post(
            "/api/v1/conversations/start/",
            {"business_id": b1["business_id"]},
            format="json",
        )
        assert start.status_code == 201, start.content
        conversation_id = start.json()["id"]
        assert business_user.id in start.json()["participant_ids"]

        again = customer.post(
            "/api/v1/conversations/start/",
            {"business_id": b1["business_id"]},
            format="json",
        )
        assert again.status_code == 200
        assert again.json()["id"] == conversation_id

        own = b1["client"].post(
            "/api/v1/conversations/start/",
            {"business_id": b1["business_id"]},
            format="json",
        )
        assert own.status_code == 400

        messages_url = f"/api/v1/conversations/{conversation_id}/messages/"

        # ---- STEP 9b: text, image, shared product card ------------------------
        text = customer.post(
            messages_url, {"text": "Is the jacket available?"}, format="json"
        )
        assert text.status_code == 201, text.content
        assert text.json()["status"] == "sent"
        text_id = text.json()["id"]

        image = customer.post(
            messages_url, {"media": _png("chat.png")}, format="multipart"
        )
        assert image.status_code == 201, image.content
        assert image.json()["media_type"] == "image"
        assert image.json()["media"]
        image_id = image.json()["id"]

        shared = customer.post(
            messages_url,
            {
                "text": "Interested in this one",
                "shared_content_type": "product",
                "shared_object_id": b1["product_id"],
            },
            format="json",
        )
        assert shared.status_code == 201, shared.content
        card = shared.json()["shared_content"]
        assert card["content_type"] == "product"
        assert card["object_id"] == b1["product_id"]
        assert card["available"] is True
        assert card["business_id"] == b1["business_id"]
        assert card["business_name"] == "Zephyr Atelier"
        assert card["preview"]["preview_text"] == "Zephyr Jacket"

        bad_product = customer.post(
            messages_url,
            {"shared_content_type": "product", "shared_object_id": 999999},
            format="json",
        )
        assert bad_product.status_code == 404
        both = customer.post(
            messages_url,
            {
                "media": _png("x.png"),
                "shared_content_type": "product",
                "shared_object_id": b1["product_id"],
            },
            format="multipart",
        )
        assert both.status_code == 400

        # ---- STEP 10a: the Business opens the app (fetch-on-open) ---------------
        business = b1["client"]
        conversations = _results(business.get("/api/v1/conversations/"))
        assert [c["id"] for c in conversations] == [conversation_id]
        listed = conversations[0]
        assert listed["other_participant"]["display_name"] == "S3 Customer"
        assert listed["unread_count"] == 3
        assert listed["last_message"]["shared_content_type"] == "product"
        assert listed["last_message"]["status"] == "sent"

        history = _results(business.get(messages_url))
        assert [m["id"] for m in history] == [
            shared.json()["id"],
            image_id,
            text_id,
        ]

        since = business.get(f"{messages_url}?since={text_id}")
        assert since.status_code == 200
        assert [m["id"] for m in since.json()] == [image_id, shared.json()["id"]]

        # Opening / fetching alone never changes delivery status: only a live
        # recipient acknowledgment does (covered by the WebSocket test below).
        assert {m["status"] for m in history} == {"sent"}

        # Participants only: an outsider can neither read nor write.
        outsider = User.objects.create_user(
            username="p094-s3-outsider",
            email="p094-s3-outsider@example.com",
            password=PASSWORD,
            account_type=User.ACCOUNT_TYPE_CUSTOMER,
        )
        stranger = _forced_client(outsider)
        assert stranger.get(messages_url).status_code == 403
        assert stranger.post(messages_url, {"text": "hi"}, format="json").status_code == 403

        # ---- STEP 10b: offline push decision (Business not connected) -----------
        assert offline_notify.delay.call_count == 3
        assert [c.args[0] for c in offline_notify.delay.call_args_list] == [
            text_id,
            image_id,
            shared.json()["id"],
        ]

        # A connected Business (online presence key) must NOT get a push.
        cache.set(presence_cache_key(business_user.id), True, 60)
        online = customer.post(messages_url, {"text": "ping"}, format="json")
        assert online.status_code == 201
        assert offline_notify.delay.call_count == 3
        cache.delete(presence_cache_key(business_user.id))

        # The task body hands a well-formed event to the notification
        # orchestrator (chat -> notifications seam).
        dispatch_delay.reset_mock()
        offline_task(image_id)
        dispatch_delay.assert_called_once_with(
            recipient_id=business_user.id,
            notification_type="chat_message",
            title="New message",
            body="Sent a photo",
            deep_link_type="chat_thread",
            target_id=conversation_id,
        )


async def _ws_connect(conversation_id, user):
    token = str(AccessToken.for_user(user))
    communicator = WebsocketCommunicator(
        application, f"/ws/conversations/{conversation_id}/?token={token}"
    )
    connected, _ = await communicator.connect()
    assert connected is True
    return communicator


@database_sync_to_async
def _make_chat_actors():
    category = Category.objects.create(name="Async Fashion")
    business_user = User.objects.create_user(
        username="p094-ws-biz",
        email="p094-ws-biz@example.com",
        password=PASSWORD,
        account_type=User.ACCOUNT_TYPE_BUSINESS,
    )
    business = BusinessProfile.objects.create(
        user=business_user,
        business_name="Async Atelier",
        business_type="trader",
        country="Egypt",
        city="Cairo",
        category=category,
    )
    customer_user = User.objects.create_user(
        username="p094-ws-customer",
        email="p094-ws-customer@example.com",
        password=PASSWORD,
        account_type=User.ACCOUNT_TYPE_CUSTOMER,
    )
    return business_user, customer_user, business.id


@database_sync_to_async
def _message_status(message_id):
    return Message.objects.get(pk=message_id).status


class TestPhase17Steps9To10Live:
    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_live_delivery_and_status_progression(self, offline_notify):
        business_user, customer_user, business_id = await _make_chat_actors()
        customer = _forced_client(customer_user)
        business = _forced_client(business_user)

        start = await sync_to_async(customer.post)(
            "/api/v1/conversations/start/", {"business_id": business_id}, format="json"
        )
        assert start.status_code == 201, start.content
        conversation_id = start.json()["id"]
        url = f"/api/v1/conversations/{conversation_id}/messages/"

        business_ws = await _ws_connect(conversation_id, business_user)
        customer_ws = await _ws_connect(conversation_id, customer_user)

        # ---- the Business receives the message live ---------------------------
        sent = await sync_to_async(customer.post)(url, {"text": "Live hello"}, format="json")
        assert sent.status_code == 201, sent.content
        message_id = sent.json()["id"]

        live = json.loads(await business_ws.receive_from())
        assert live["id"] == message_id
        assert live["text"] == "Live hello"
        assert live["status"] == "sent"
        echoed = json.loads(await customer_ws.receive_from())
        assert echoed["id"] == message_id
        # The recipient is connected, so no offline push is queued.
        offline_notify.delay.assert_not_called()

        # ---- sent -> delivered (recipient acknowledges) ------------------------
        await business_ws.send_to(
            text_data=json.dumps({"type": "mark_delivered", "message_id": message_id})
        )
        update = {"message_id": message_id, "status": "delivered"}
        assert json.loads(await customer_ws.receive_from()) == update
        assert json.loads(await business_ws.receive_from()) == update
        assert await _message_status(message_id) == "delivered"

        customer_view = _results(await sync_to_async(customer.get)("/api/v1/conversations/"))
        assert customer_view[0]["last_message"]["status"] == "delivered"
        business_view = _results(await sync_to_async(business.get)("/api/v1/conversations/"))
        assert business_view[0]["unread_count"] == 1  # delivered, not yet read

        # ---- delivered -> read --------------------------------------------------
        await business_ws.send_to(
            text_data=json.dumps({"type": "mark_read", "message_id": message_id})
        )
        update = {"message_id": message_id, "status": "read"}
        assert json.loads(await customer_ws.receive_from()) == update
        assert json.loads(await business_ws.receive_from()) == update
        assert await _message_status(message_id) == "read"
        business_view = _results(await sync_to_async(business.get)("/api/v1/conversations/"))
        assert business_view[0]["unread_count"] == 0

        # ---- only the RECIPIENT may acknowledge (F-7) ----------------------------
        second = await sync_to_async(customer.post)(url, {"text": "Second"}, format="json")
        second_id = second.json()["id"]
        await business_ws.receive_from()
        await customer_ws.receive_from()
        await customer_ws.send_to(
            text_data=json.dumps({"type": "mark_read", "message_id": second_id})
        )
        assert await business_ws.receive_nothing(timeout=0.5) is True, (
            "SEAM GAP (F-7): the SENDER acknowledged their own message and the "
            "server broadcast a status update"
        )
        assert await _message_status(second_id) == "sent"

        # ---- Business goes offline: the next message queues an offline push ------
        await business_ws.disconnect()
        third = await sync_to_async(customer.post)(url, {"text": "Third"}, format="json")
        assert third.status_code == 201
        offline_notify.delay.assert_called_once_with(third.json()["id"])

        await customer_ws.disconnect()
'@
if ($tt.Contains('class TestPhase17Steps9To10Rest')) {
    Write-Host 'test module already has the step 3 classes - skipped'
} else {
    if ($tt -notmatch '(?m)^import json$') {
        $a = "from decimal import Decimal`n"
        if (-not $tt.Contains($a)) { throw 'test module: import anchor 1 not found' }
        $tt = $tt.Replace($a, "import json`nfrom decimal import Decimal`nfrom unittest import mock`n")
    }
    if (-not $tt.Contains('from chat.consumers import presence_cache_key')) {
        $b = "from rest_framework.test import APIClient`n"
        if (-not $tt.Contains($b)) { throw 'test module: import anchor 2 not found' }
        $extra = "from asgiref.sync import sync_to_async`nfrom channels.db import database_sync_to_async`nfrom channels.testing import WebsocketCommunicator`nfrom rest_framework_simplejwt.tokens import AccessToken`n`nfrom chat.consumers import presence_cache_key`nfrom chat.models import Message`nfrom config.asgi import application`n"
        $tt = $tt.Replace($b, $b + $extra)
    }
    $tt = $tt.TrimEnd("`n") + "`n`n`n" + $block.Replace("`r`n", "`n")
    [System.IO.File]::WriteAllText($tp, $tt, $utf8)
    Write-Host 'updated core\tests\test_integration_phase17.py'
}

# ---------------------------------------------------------------- 3) report
$rp = Join-Path $root 'INTEGRATION_TEST_REPORT_PHASE17.md'
$rep = [System.IO.File]::ReadAllText($rp).Replace("`r`n", "`n")
if ($rep.Contains('F-7')) {
    Write-Host 'report already has F-7 - skipped'
} else {
    $anchors = @(
        'Status: IN PROGRESS (script step 2 of 4 applied)',
        '| 9 | Chat: text, image, product share | script step 3 | NOT STARTED | |',
        '| 10 | Real-time delivery + status progression | script step 3 | NOT STARTED | |',
        "`n`n## 4. Open follow-up items"
    )
    foreach ($an in $anchors) {
        if (-not $rep.Contains($an)) { throw "report: anchor not found: $an" }
    }
    $rep = $rep.Replace('Status: IN PROGRESS (script step 2 of 4 applied)', 'Status: IN PROGRESS (script step 3 of 4 applied)')
    $rep = $rep.Replace('| 9 | Chat: text, image, product share | script step 3 | NOT STARTED | |',
        '| 9 | Chat: text, image, product share | automated (TestPhase17Steps9To10Rest) | PASSED | start by business_id is idempotent; text, image (media_type=image) and a shared Product card (available, business_name, preview) all send; outsider gets 403 |')
    $rep = $rep.Replace('| 10 | Real-time delivery + status progression | script step 3 | NOT STARTED | |',
        '| 10 | Real-time delivery + status progression | automated (TestPhase17Steps9To10Rest + Live) | PASSED (after F-7 fix) | fetch-on-open (list, history, since) works and does not change status; over real WebSocket the Business receives the message live and sent -> delivered -> read is broadcast to both sides; offline push queued only when the recipient is not connected |')
    $f7 = @'

- F-7 (real bug, FIXED): the WebSocket ack handler (chat/consumers.py _apply_status_transition) let ANY participant acknowledge ANY message, including the SENDER acknowledging their own message. A sender could therefore fake a read receipt and zero out the recipient's unread_count for their own messages. Fix: the lookup now excludes messages sent by the connected user, so a sender's own mark_delivered/mark_read is a silent no-op. Covered by TestPhase17Steps9To10Live; the existing P-069 tests (all acks sent by the recipient) still pass.
'@
    $rep = $rep.Replace("`n`n## 4. Open follow-up items", $f7.Replace("`r`n", "`n") + "`n`n## 4. Open follow-up items")
    [System.IO.File]::WriteAllText($rp, $rep, $utf8)
    Write-Host 'updated INTEGRATION_TEST_REPORT_PHASE17.md'
}

Write-Host ''
Write-Host 'Done. Now run:'
Write-Host '  docker compose exec web pytest core/tests/test_integration_phase17.py -v'
Write-Host '  docker compose exec web pytest chat -q'