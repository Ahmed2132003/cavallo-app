"""
BE-07 - storage-plan checkout (POST/GET payments/subscriptions/checkout/)
and the webhook hook that activates the storage plan.

Checkout tests use FakeGateway through settings.PAYMENT_GATEWAY.
Webhook tests build the pending rows with the service (FakeGateway
injected) and then post a REAL signed Paymob webhook, so the real HMAC
check runs. Activation is observed with spies that WRAP the real
functions.
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from monetization.models import FeaturedSubscription
from monetization.models import Subscription as StorageSubscription
from monetization.services import calculate_custom_price, custom_quota_bytes
from payments import webhooks as webhooks_module
from payments.gateways.base import PaymentGatewayError
from payments.models import KIND_STORAGE, Subscription, Transaction
from payments.storage_services import (
    initiate_storage_payment,
    quote_storage_plan,
)
from payments.storage_activation import STORAGE_PERIOD_DAYS
from payments.tests.factories import make_business, make_plan, make_storage_plan
from payments.tests.fake_gateway import FakeGateway
from payments.tests.webhook_helpers import SECRET, build_payload, post_webhook

pytestmark = pytest.mark.django_db

URL = "/api/v1/payments/subscriptions/checkout/"
STATUS_URL = URL + "{}/status/"
User = get_user_model()


class FailingGateway(FakeGateway):
    def initiate_payment(self, subscription):
        raise PaymentGatewayError("upstream said: secret-detail-xyz")


@pytest.fixture(autouse=True)
def _webhook_secret(settings):
    settings.PAYMOB_WEBHOOK_SECRET = SECRET


@pytest.fixture
def fake_gateway(settings):
    settings.PAYMENT_GATEWAY = "payments.tests.fake_gateway.FakeGateway"


@pytest.fixture
def storage_spy():
    with mock.patch.object(
        webhooks_module,
        "activate_storage_subscription",
        wraps=webhooks_module.activate_storage_subscription,
    ) as spy:
        yield spy


@pytest.fixture
def featured_spy():
    with mock.patch.object(
        webhooks_module,
        "activate_subscription",
        wraps=webhooks_module.activate_subscription,
    ) as spy:
        yield spy


def _client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _error(response):
    return response.json()["error"]


def _pay(business, code="pro", gb=None):
    """A pending storage payment, as checkout leaves it."""
    quote = quote_storage_plan(code, gb)
    payment, _url = initiate_storage_payment(business, quote, gateway=FakeGateway())
    return payment, quote


def _payload(payment, quote, **overrides):
    return build_payload(
        order_id=555,
        merchant_order_id=f"payments-sub-{payment.pk}",
        amount_cents=int(quote.amount * 100),
        **overrides,
    )


# ------------------------------------------------------------- checkout


def test_regular_plan_checkout_charges_the_server_side_price(fake_gateway):
    make_storage_plan("pro", price_egp="199.00")
    owner = make_business("owner")

    response = _client_for(owner.user).post(
        URL,
        {"plan": "pro", "amount_egp": "1.00", "price_egp": "1.00", "amount": 1},
        format="json",
    )

    assert response.status_code == 201
    payment = Subscription.objects.get()
    assert response.json() == {
        "payment_id": payment.pk,
        "checkout_url": f"https://fake-pay.example/checkout/{payment.pk}",
        "amount_egp": "199.00",
    }
    assert payment.kind == KIND_STORAGE
    assert payment.business_id == owner.pk
    assert payment.plan.code == "pro"
    assert payment.gb is None
    assert payment.status == "pending"
    assert payment.gateway_reference == f"fake-{payment.pk}"
    txn = Transaction.objects.get()
    assert txn.amount == Decimal("199.00")
    assert txn.currency == "EGP"
    assert txn.transaction_id is None
    assert txn.status == "pending"


def test_checkout_never_activates_anything(fake_gateway):
    make_storage_plan("pro")
    owner = make_business("owner")
    before = StorageSubscription.objects.filter(business=owner).count()

    response = _client_for(owner.user).post(URL, {"plan": "pro"}, format="json")

    assert response.status_code == 201
    assert FeaturedSubscription.objects.count() == 0
    assert StorageSubscription.objects.filter(business=owner).count() == before
    assert not StorageSubscription.objects.filter(
        business=owner, status="active"
    ).exists()


def test_custom_plan_amount_is_20gb_630_egp(fake_gateway):
    make_storage_plan("infinity", price_egp=None, storage_gb=None, is_custom=True)
    owner = make_business("owner")

    response = _client_for(owner.user).post(
        URL, {"plan": "infinity", "gb": 20}, format="json"
    )

    assert response.status_code == 201
    assert response.json()["amount_egp"] == "630.00"
    payment = Subscription.objects.get()
    assert payment.gb == Decimal("20.00")
    assert Transaction.objects.get().amount == Decimal("630.00")


@pytest.mark.parametrize("gb", [20, "20", "20.5", 20.5, "100"])
def test_custom_amount_always_equals_the_pricing_function(fake_gateway, gb):
    make_storage_plan("infinity", price_egp=None, storage_gb=None, is_custom=True)
    owner = make_business("owner")

    response = _client_for(owner.user).post(
        URL, {"plan": "infinity", "gb": gb}, format="json"
    )

    assert response.status_code == 201
    expected = calculate_custom_price(gb)
    assert response.json()["amount_egp"] == str(expected)
    assert Transaction.objects.get().amount == expected


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"plan": ""},
        {"plan": "nope"},
        {"plan": "free"},
        {"plan": "pro", "gb": 10},
        {"plan": "infinity"},
        {"plan": "infinity", "gb": "abc"},
        {"plan": "infinity", "gb": -5},
        {"plan": "infinity", "gb": True},
        {"plan": "infinity", "gb": 999999},
    ],
    ids=[
        "missing",
        "blank",
        "unknown-code",
        "free-not-purchasable",
        "gb-on-fixed-plan",
        "custom-without-gb",
        "gb-not-a-number",
        "gb-negative",
        "gb-bool",
        "gb-above-max",
    ],
)
def test_bad_checkout_body_gets_400_and_creates_nothing(fake_gateway, body):
    make_storage_plan("pro")
    make_storage_plan("free", price_egp="0.00", storage_gb=1)
    make_storage_plan("infinity", price_egp=None, storage_gb=None, is_custom=True)
    owner = make_business("owner")

    response = _client_for(owner.user).post(URL, body, format="json")

    assert response.status_code == 400
    assert _error(response)["code"] == "VALIDATION_ERROR"
    assert Subscription.objects.count() == 0
    assert Transaction.objects.count() == 0


def test_plan_without_a_price_cannot_be_purchased(fake_gateway):
    make_storage_plan("max", price_egp=None)
    owner = make_business("owner")

    response = _client_for(owner.user).post(URL, {"plan": "max"}, format="json")

    assert response.status_code == 400
    assert "plan" in _error(response)["fields"]
    assert Subscription.objects.count() == 0


def test_gateway_failure_gives_503_hides_details_and_marks_rows_failed(settings):
    settings.PAYMENT_GATEWAY = "payments.tests.test_subscription_checkout.FailingGateway"
    make_storage_plan("pro")
    owner = make_business("owner")

    response = _client_for(owner.user).post(URL, {"plan": "pro"}, format="json")

    assert response.status_code == 503
    assert _error(response)["code"] == "SERVICE_UNAVAILABLE"
    assert "secret-detail-xyz" not in response.content.decode()
    assert Subscription.objects.get().status == "failed"
    assert Transaction.objects.get().status == "failed"
    assert FeaturedSubscription.objects.count() == 0


def test_business_account_without_profile_gets_404(fake_gateway):
    make_storage_plan("pro")
    user = User.objects.create_user(
        username="noprofile-be07@example.com",
        email="noprofile-be07@example.com",
        password="testpass123",
        account_type="business",
    )

    response = _client_for(user).post(URL, {"plan": "pro"}, format="json")

    assert response.status_code == 404
    assert "business profile" in _error(response)["message"]
    assert Subscription.objects.count() == 0


# --------------------------------------------------------------- status


def test_status_goes_from_pending_to_completed_for_the_owner():
    make_storage_plan("pro", price_egp="199.00")
    owner = make_business("owner")
    payment, quote = _pay(owner)
    client = _client_for(owner.user)

    pending = client.get(STATUS_URL.format(payment.pk))

    assert pending.status_code == 200
    assert pending.json() == {
        "payment_id": payment.pk,
        "status": "pending",
        "plan": "pro",
        "gb": None,
        "amount_egp": "199.00",
    }

    assert post_webhook(APIClient(), _payload(payment, quote)).status_code == 200

    assert client.get(STATUS_URL.format(payment.pk)).json()["status"] == "completed"


def test_status_of_a_custom_payment_reports_gb_and_amount():
    make_storage_plan("infinity", price_egp=None, storage_gb=None, is_custom=True)
    owner = make_business("owner")
    payment, _quote = _pay(owner, "infinity", 20)

    body = _client_for(owner.user).get(STATUS_URL.format(payment.pk)).json()

    assert body["gb"] == "20.00"
    assert body["amount_egp"] == "630.00"
    assert body["plan"] == "infinity"


def test_another_business_cannot_see_the_payment():
    make_storage_plan("pro")
    owner = make_business("owner")
    other = make_business("other", phone_number="+201001234568")
    payment, _quote = _pay(owner)

    response = _client_for(other.user).get(STATUS_URL.format(payment.pk))

    assert response.status_code == 404
    assert _error(response)["code"] == "NOT_FOUND"


def test_a_featured_payment_is_not_exposed_by_the_storage_status_endpoint():
    owner = make_business("owner")
    featured_payment = Subscription.objects.create(business=owner, plan=make_plan())

    response = _client_for(owner.user).get(STATUS_URL.format(featured_payment.pk))

    assert response.status_code == 404


# -------------------------------------------------------------- webhook


def test_successful_webhook_activates_the_storage_plan_once(
    storage_spy, featured_spy
):
    plan = make_storage_plan("pro", storage_gb=50)
    owner = make_business("owner")
    payment, quote = _pay(owner)

    response = post_webhook(APIClient(), _payload(payment, quote))

    assert response.status_code == 200
    payment.refresh_from_db()
    assert payment.status == "completed"
    storage_spy.assert_called_once()
    kwargs = storage_spy.call_args.kwargs
    assert kwargs["business"].pk == owner.pk
    assert kwargs["plan"].pk == plan.pk
    assert kwargs["gb"] is None
    assert kwargs["payment"].pk == payment.pk
    featured_spy.assert_not_called()

    sub = StorageSubscription.objects.get(business=owner)
    assert sub.status == "active"
    assert sub.plan_id == plan.pk
    assert sub.quota_bytes == 50 * 1024**3
    assert sub.purge_at is None
    assert sub.current_period_end - sub.current_period_start == timedelta(
        days=STORAGE_PERIOD_DAYS
    )
    assert FeaturedSubscription.objects.count() == 0
    owner.refresh_from_db()
    assert owner.is_featured is False


def test_successful_custom_payment_applies_the_paid_for_quota(storage_spy):
    make_storage_plan("infinity", price_egp=None, storage_gb=None, is_custom=True)
    owner = make_business("owner")
    payment, quote = _pay(owner, "infinity", 20)

    response = post_webhook(APIClient(), _payload(payment, quote))

    assert response.status_code == 200
    storage_spy.assert_called_once()
    sub = StorageSubscription.objects.get(business=owner)
    assert sub.status == "active"
    assert sub.quota_bytes == custom_quota_bytes(20)
    assert sub.custom_gb == 20


def test_existing_trial_subscription_is_upgraded_in_place():
    free = make_storage_plan("free", price_egp="0.00", storage_gb=1)
    make_storage_plan("pro", storage_gb=50)
    owner = make_business("owner")
    now = timezone.now()
    StorageSubscription.objects.update_or_create(
        business=owner,
        defaults={
            "plan": free,
            "status": "trialing",
            "started_at": now,
            "trial_ends_at": now + timedelta(days=3),
            "quota_bytes": 1024**3,
            "purge_at": now + timedelta(days=10),
        },
    )
    payment, quote = _pay(owner)

    assert post_webhook(APIClient(), _payload(payment, quote)).status_code == 200

    assert StorageSubscription.objects.filter(business=owner).count() == 1
    sub = StorageSubscription.objects.get(business=owner)
    assert sub.status == "active"
    assert sub.plan.code == "pro"
    assert sub.quota_bytes == 50 * 1024**3
    assert sub.purge_at is None


def test_duplicate_webhook_does_not_activate_twice(storage_spy):
    make_storage_plan("pro")
    owner = make_business("owner")
    payment, quote = _pay(owner)
    payload = _payload(payment, quote)

    first = post_webhook(APIClient(), payload)
    period_end = StorageSubscription.objects.get(business=owner).current_period_end
    second = post_webhook(APIClient(), payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert storage_spy.call_count == 1
    assert (
        StorageSubscription.objects.get(business=owner).current_period_end
        == period_end
    )
    assert Transaction.objects.filter(subscription=payment).count() == 1


def test_failed_payment_does_not_activate(storage_spy, featured_spy):
    make_storage_plan("pro")
    owner = make_business("owner")
    payment, quote = _pay(owner)

    response = post_webhook(APIClient(), _payload(payment, quote, success=False))

    assert response.status_code == 200
    payment.refresh_from_db()
    assert payment.status == "failed"
    storage_spy.assert_not_called()
    featured_spy.assert_not_called()
    assert not StorageSubscription.objects.filter(
        business=owner, status="active"
    ).exists()


@pytest.mark.parametrize("wrong_cents", [100, 19900])
def test_amount_mismatch_on_a_custom_payment_is_not_activated(
    storage_spy, wrong_cents
):
    make_storage_plan("infinity", price_egp=None, storage_gb=None, is_custom=True)
    owner = make_business("owner")
    payment, quote = _pay(owner, "infinity", 20)  # server amount: 630.00
    payload = _payload(payment, quote)
    payload["obj"]["amount_cents"] = wrong_cents

    response = post_webhook(APIClient(), payload)

    assert response.status_code == 200
    payment.refresh_from_db()
    assert payment.status == "pending"
    storage_spy.assert_not_called()
    assert not StorageSubscription.objects.filter(
        business=owner, status="active"
    ).exists()