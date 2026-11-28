"""BE-03: Infinity custom pricing: calculation, bounds, parsing and the quote API."""

from decimal import Decimal

import pytest
from django.test import override_settings
from rest_framework.exceptions import ValidationError

from core.tests.sweep_factories import client_for, make_business
from monetization.constants import BYTES_PER_GB
from monetization.models import Plan, Subscription
from monetization.services import (
    calculate_custom_price,
    create_free_subscription,
    custom_quota_bytes,
    is_locked,
    parse_gb,
)

URL = "/api/v1/monetization/subscriptions/custom-quote/"


@pytest.mark.parametrize(
    "gb, expected",
    [
        ("15.87", "500"),  # 499.905 -> ceil 500
        ("20", "630"),
        ("13", "500"),  # 409.5 -> ceil 410 -> raised to the minimum
        ("14", "500"),  # 441
        ("13.01", "500"),
        ("15.88", "501"),  # 500.22 -> ceil 501
        ("16", "504"),
        ("20.5", "646"),  # 645.75 -> ceil 646
        ("500", "15750"),
    ],
)
def test_price_examples(gb, expected):
    price = calculate_custom_price(Decimal(gb))

    assert price == Decimal(expected)
    assert price.as_tuple().exponent == -2


def test_float_and_int_inputs_are_accepted_without_float_error():
    assert calculate_custom_price(15.87) == Decimal("500")
    assert calculate_custom_price(20) == Decimal("630")
    assert calculate_custom_price("20") == Decimal("630")


@pytest.mark.parametrize("gb", ["12.99", "12", "0", "0.5"])
def test_below_minimum_is_rejected(gb):
    with pytest.raises(ValidationError):
        calculate_custom_price(gb)


def test_maximum_boundary():
    assert calculate_custom_price("500") == Decimal("15750")
    with pytest.raises(ValidationError):
        calculate_custom_price("500.01")


@pytest.mark.parametrize(
    "bad",
    [None, "", "abc", "-5", "+20", "1e2", "nan", "Infinity", "20.123", True, [20]],
)
def test_invalid_input_is_rejected(bad):
    with pytest.raises(ValidationError):
        calculate_custom_price(bad)


def test_two_decimals_ok_three_rejected():
    assert parse_gb("20.55") == Decimal("20.55")
    with pytest.raises(ValidationError):
        parse_gb("20.555")


def test_settings_are_honoured_at_call_time():
    with override_settings(CUSTOM_PRICE_PER_GB="40"):
        assert calculate_custom_price("20") == Decimal("800")
    with override_settings(CUSTOM_MIN_GB="20"):
        with pytest.raises(ValidationError):
            calculate_custom_price("19.99")
    with override_settings(CUSTOM_MIN_PRICE="700"):
        assert calculate_custom_price("20") == Decimal("700")
    with override_settings(CUSTOM_MAX_GB="100"):
        with pytest.raises(ValidationError):
            calculate_custom_price("100.01")


def test_quota_bytes_uses_binary_gb():
    assert BYTES_PER_GB == 1024**3
    assert custom_quota_bytes("20") == 20 * 1024**3
    # 15.87 GB, fraction of a byte dropped (floor).
    assert custom_quota_bytes("15.87") == 1587 * BYTES_PER_GB // 100


@pytest.mark.django_db
class TestCustomQuoteApi:
    def test_get_returns_the_quote(self):
        client = client_for(make_business().user)

        response = client.get(URL, {"gb": "20"})

        assert response.status_code == 200
        assert response.json() == {
            "gb": "20.00",
            "price_egp": "630.00",
            "quota_bytes": 20 * BYTES_PER_GB,
            "is_featured": True,
        }

    def test_post_accepts_a_json_number(self):
        client = client_for(make_business().user)

        response = client.post(URL, {"gb": 15.87}, format="json")

        assert response.status_code == 200
        body = response.json()
        assert body["gb"] == "15.87"
        assert body["price_egp"] == "500.00"
        assert body["quota_bytes"] == 1587 * BYTES_PER_GB // 100
        assert body["is_featured"] is True

    @pytest.mark.parametrize(
        "params",
        [{}, {"gb": ""}, {"gb": "abc"}, {"gb": "12.99"}, {"gb": "500.01"}, {"gb": "20.123"}],
    )
    def test_invalid_gb_is_a_400_with_the_error_envelope(self, params):
        client = client_for(make_business().user)

        response = client.get(URL, params)

        assert response.status_code == 400
        assert "error" in response.json()

    def test_post_with_missing_gb_is_a_400(self):
        client = client_for(make_business().user)

        response = client.post(URL, {}, format="json")

        assert response.status_code == 400

    def test_locked_merchant_can_still_get_a_quote(self):
        business = make_business()
        Plan.objects.get_or_create(
            code="free",
            defaults={
                "name": "Free",
                "duration_days": 30,
                "price": Decimal("0.00"),
                "currency": "EGP",
                "trial_days": 15,
            },
        )
        create_free_subscription(business)
        Subscription.objects.filter(business_id=business.pk).update(
            status=Subscription.STATUS_LOCKED
        )
        assert is_locked(business) is True

        response = client_for(business.user).get(URL, {"gb": "20"})

        assert response.status_code == 200
        assert response.json()["price_egp"] == "630.00"