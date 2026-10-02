"""
Part P-092 (STEP 2) - tests for POST /api/v1/payments/initiate/.

The gateway is always a test double (settings.PAYMENT_GATEWAY), so no
network call is ever made.
"""

import ast
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

import payments.initiate_views as initiate_views_module
from monetization.models import FeaturedSubscription
from payments.gateways.base import PaymentGatewayError
from payments.models import Subscription, Transaction
from payments.tests.factories import make_business, make_plan
from payments.tests.fake_gateway import FakeGateway

pytestmark = pytest.mark.django_db

URL = "/api/v1/payments/initiate/"
User = get_user_model()


class FailingGateway(FakeGateway):
    def initiate_payment(self, subscription):
        raise PaymentGatewayError("upstream said: secret-detail-xyz")


@pytest.fixture(autouse=True)
def fake_gateway(settings):
    settings.PAYMENT_GATEWAY = "payments.tests.fake_gateway.FakeGateway"


def _client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _error(response):
    return response.json()["error"]


def test_url_name_resolves_to_documented_path():
    assert reverse("payment-initiate") == URL


def test_anonymous_request_is_rejected_and_creates_nothing():
    plan = make_plan()

    response = APIClient().post(URL, {"plan_id": plan.pk}, format="json")

    assert response.status_code == 401
    assert _error(response)["code"] == "AUTHENTICATION_FAILED"
    assert Subscription.objects.count() == 0


def test_business_owner_gets_payment_url_and_pending_payment_rows():
    profile = make_business("owner")
    plan = make_plan(price="250.00", currency="EGP")

    response = _client_for(profile.user).post(URL, {"plan_id": plan.pk}, format="json")

    assert response.status_code == 201
    sub = Subscription.objects.get()
    assert response.json() == {
        "payment_url": f"https://fake-pay.example/checkout/{sub.pk}"
    }
    assert sub.business_id == profile.pk
    assert sub.plan_id == plan.pk
    assert sub.status == "pending"
    assert sub.gateway_reference == f"fake-{sub.pk}"
    txn = Transaction.objects.get()
    assert txn.subscription_id == sub.pk
    assert txn.transaction_id is None
    assert txn.amount == Decimal("250.00")
    assert txn.currency == "EGP"
    assert txn.status == "pending"


def test_initiation_never_activates_featured_status():
    profile = make_business("owner")
    plan = make_plan()

    response = _client_for(profile.user).post(URL, {"plan_id": plan.pk}, format="json")

    assert response.status_code == 201
    assert FeaturedSubscription.objects.count() == 0
    profile.refresh_from_db()
    assert profile.is_featured is False


def test_view_calls_the_service_with_the_callers_business_and_the_plan():
    profile = make_business("owner")
    plan = make_plan()

    with mock.patch(
        "payments.initiate_views.initiate_subscription_payment",
        return_value="https://pay.example/abc",
    ) as service:
        response = _client_for(profile.user).post(
            URL, {"plan_id": plan.pk}, format="json"
        )

    assert response.status_code == 201
    assert response.json() == {"payment_url": "https://pay.example/abc"}
    service.assert_called_once_with(profile, plan)


def test_client_cannot_choose_the_buying_business():
    me = make_business("me", phone_number="+201001234567")
    other = make_business("other", phone_number="+201001234568")
    plan = make_plan()

    response = _client_for(me.user).post(
        URL,
        {"plan_id": plan.pk, "business_id": other.pk, "business": other.pk},
        format="json",
    )

    assert response.status_code == 201
    assert Subscription.objects.get().business_id == me.pk
    assert Subscription.objects.filter(business=other).count() == 0


def test_customer_account_gets_403_and_creates_nothing():
    user = User.objects.create_user(
        username="cust@example.com",
        email="cust@example.com",
        password="testpass123",
        account_type="customer",
    )
    plan = make_plan()

    response = _client_for(user).post(URL, {"plan_id": plan.pk}, format="json")

    assert response.status_code == 403
    assert _error(response)["code"] == "PERMISSION_DENIED"
    assert Subscription.objects.count() == 0


def test_business_account_without_profile_gets_404_and_creates_nothing():
    user = User.objects.create_user(
        username="noprofile@example.com",
        email="noprofile@example.com",
        password="testpass123",
        account_type="business",
    )
    plan = make_plan()

    response = _client_for(user).post(URL, {"plan_id": plan.pk}, format="json")

    assert response.status_code == 404
    assert _error(response)["code"] == "NOT_FOUND"
    assert "business profile" in _error(response)["message"]
    assert Subscription.objects.count() == 0


@pytest.mark.parametrize(
    "body",
    [{}, {"plan_id": None}, {"plan_id": "abc"}, {"plan_id": 999999999}],
    ids=["missing", "null", "not-a-number", "unknown-plan"],
)
def test_bad_plan_id_gets_400_with_field_error_and_creates_nothing(body):
    profile = make_business("owner")
    make_plan()

    response = _client_for(profile.user).post(URL, body, format="json")

    assert response.status_code == 400
    assert _error(response)["code"] == "VALIDATION_ERROR"
    assert "plan_id" in _error(response)["fields"]
    assert Subscription.objects.count() == 0


def test_get_is_not_allowed():
    profile = make_business("owner")

    response = _client_for(profile.user).get(URL)

    assert response.status_code == 405


def test_gateway_failure_gives_503_hides_details_and_marks_rows_failed(settings):
    settings.PAYMENT_GATEWAY = "payments.tests.test_initiate_api.FailingGateway"
    profile = make_business("owner")
    plan = make_plan()

    response = _client_for(profile.user).post(URL, {"plan_id": plan.pk}, format="json")

    assert response.status_code == 503
    assert _error(response)["code"] == "SERVICE_UNAVAILABLE"
    assert "secret-detail-xyz" not in response.content.decode()
    assert Subscription.objects.get().status == "failed"
    assert Transaction.objects.get().status == "failed"
    assert FeaturedSubscription.objects.count() == 0


def test_unconfigured_paymob_gives_503_not_500(settings):
    settings.PAYMENT_GATEWAY = "payments.gateways.paymob.PaymobGateway"
    settings.PAYMOB_SECRET_KEY = ""
    settings.PAYMOB_PUBLIC_KEY = ""
    settings.PAYMOB_INTEGRATION_ID = ""
    profile = make_business("owner")
    plan = make_plan()

    response = _client_for(profile.user).post(URL, {"plan_id": plan.pk}, format="json")

    assert response.status_code == 503
    assert _error(response)["code"] == "SERVICE_UNAVAILABLE"
    assert Subscription.objects.get().status == "failed"


def test_module_boundaries():
    tree = ast.parse(Path(initiate_views_module.__file__).read_text(encoding="utf-8"))
    imported, used = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            used.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)

    assert "payments.gateways.paymob" not in imported
    assert not any(name.startswith("monetization") for name in imported)
    assert not used & {
        "activate_subscription",
        "FeaturedSubscription",
        "is_featured",
        "is_active",
    }
