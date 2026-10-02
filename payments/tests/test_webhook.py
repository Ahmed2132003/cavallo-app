"""
Part P-090 (STEP 1): webhook signature gate + payload parsing.

The single most important test here: an invalid signature is rejected
with 400 and leaves ZERO side effects, verified by comparing direct DB
snapshots (not just the response code).
"""

import json
from unittest import mock

import pytest
from rest_framework.test import APIClient

from payments import views as views_module
from payments.tests.webhook_helpers import (
    SECRET,
    WEBHOOK_URL,
    build_payload,
    db_snapshot,
    make_pending_payment,
    post_webhook,
    sign,
)
from payments.views import WebhookEvent, _parse_webhook_payload

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    # No credentials at all: the endpoint must work unauthenticated.
    return APIClient()


@pytest.fixture
def configured(settings):
    settings.PAYMOB_WEBHOOK_SECRET = SECRET
    return settings


def _tampered_after_signing():
    payload = build_payload()
    signature = sign(payload)
    payload["obj"]["amount_cents"] = 1  # attacker edits the amount
    return payload, signature


class TestInvalidSignatureIsRejectedWithZeroSideEffects:
    @pytest.mark.parametrize(
        "case",
        ["wrong_secret", "tampered_body", "missing_hmac", "garbage_hmac", "empty_hmac"],
    )
    def test_rejected_and_db_untouched(self, client, configured, case):
        make_pending_payment()
        before = db_snapshot()
        payload = build_payload()

        if case == "wrong_secret":
            response = post_webhook(client, payload, secret="attacker-secret")
        elif case == "tampered_body":
            tampered, signature = _tampered_after_signing()
            response = post_webhook(client, tampered, hmac_value=signature)
        elif case == "missing_hmac":
            response = post_webhook(client, payload, send_hmac=False)
        elif case == "garbage_hmac":
            response = post_webhook(client, payload, hmac_value="deadbeef")
        else:
            response = post_webhook(client, payload, hmac_value="")

        assert response.status_code == 400
        assert db_snapshot() == before

    def test_unconfigured_secret_rejects_even_a_well_formed_signature(
        self, client, settings
    ):
        settings.PAYMOB_WEBHOOK_SECRET = ""
        make_pending_payment()
        before = db_snapshot()
        payload = build_payload()
        # Signed with the empty key an attacker would guess.
        response = post_webhook(client, payload, hmac_value=sign(payload, ""))
        assert response.status_code == 400
        assert db_snapshot() == before

    def test_non_json_body_is_rejected(self, client, configured):
        make_pending_payment()
        before = db_snapshot()
        response = client.post(
            WEBHOOK_URL + "?hmac=abc", data="not json", content_type="application/json"
        )
        assert response.status_code == 400
        assert db_snapshot() == before

    def test_signature_is_checked_before_the_payload_is_parsed(
        self, client, configured
    ):
        with mock.patch.object(views_module, "_parse_webhook_payload") as parse:
            response = post_webhook(client, build_payload(), hmac_value="deadbeef")
        assert response.status_code == 400
        parse.assert_not_called()


class TestVerifiedPayload:

    def test_no_authentication_is_required(self, client, configured):
        response = post_webhook(client, build_payload())
        assert response.status_code not in (401, 403)

    def test_verified_payload_without_an_id_is_rejected(self, client, configured):
        payload = build_payload()
        del payload["obj"]["id"]
        response = post_webhook(client, payload)  # correctly signed
        assert response.status_code == 400

    def test_gateway_comes_from_the_configured_setting(self, client, settings):
        # FakeGateway accepts only the literal signature "valid": proves the
        # view goes through get_gateway(), not a hard-wired Paymob.
        settings.PAYMENT_GATEWAY = "payments.tests.fake_gateway.FakeGateway"
        ok = post_webhook(client, build_payload(), hmac_value="valid")
        bad = post_webhook(client, build_payload(), hmac_value="nope")
        assert ok.status_code == 200
        assert bad.status_code == 400

    def test_only_post_is_allowed(self, client, configured):
        assert client.get(WEBHOOK_URL).status_code == 405


class TestParsePayload:
    def _raw(self, **kwargs):
        return json.dumps(build_payload(**kwargs)).encode()

    def test_extracts_the_fields_we_use(self):
        event = _parse_webhook_payload(self._raw())
        assert event == WebhookEvent(
            transaction_id="900001",
            order_id="4778239",
            merchant_order_id="payments-sub-1",
            success=True,
            pending=False,
            is_voided=False,
            is_refunded=False,
            amount_cents=25000,
            currency="EGP",
        )

    def test_failed_payment_is_not_success(self):
        assert _parse_webhook_payload(self._raw(success=False)).success is False

    def test_success_is_strict_a_false_string_is_not_success(self):
        payload = build_payload()
        payload["obj"]["success"] = "false"
        event = _parse_webhook_payload(json.dumps(payload).encode())
        assert event.success is False

    @pytest.mark.parametrize(
        "raw", [b"not json", b"[]", b'{"obj": []}', b'{"obj": {}}', b'{"x": 1}']
    )
    def test_unusable_shapes_return_none(self, raw):
        assert _parse_webhook_payload(raw) is None
