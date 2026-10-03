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
