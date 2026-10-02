"""
Part P-092 (STEP 1) - tests for GET /api/v1/monetization/plans/.

The endpoint is public (the Web Dashboard shows pricing before login),
read-only, unpaginated and ordered by duration then id.
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from monetization.models import Plan

pytestmark = pytest.mark.django_db

URL = "/api/v1/monetization/plans/"
User = get_user_model()


def _make_plan(name, days, price, currency="EGP"):
    return Plan.objects.create(
        name=name,
        duration_days=days,
        price=Decimal(price),
        currency=currency,
    )


def test_url_name_resolves_to_documented_path():
    assert reverse("monetization:plan-list") == URL


def test_anonymous_request_returns_200_and_empty_list_when_no_plans():
    response = APIClient().get(URL)

    assert response.status_code == 200
    assert response.json() == []


def test_returns_every_plan_with_exactly_the_public_fields():
    plan = _make_plan("Featured 30", 30, "250.00")

    response = APIClient().get(URL)

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": plan.id,
            "name": "Featured 30",
            "duration_days": 30,
            "price": "250.00",
            "currency": "EGP",
        }
    ]


def test_response_is_a_plain_array_not_a_paginated_object():
    for days in (7, 30, 90):
        _make_plan(f"Featured {days}", days, "100.00")

    body = APIClient().get(URL).json()

    assert isinstance(body, list)
    assert len(body) == 3


def test_plans_are_ordered_by_duration_then_id():
    p90 = _make_plan("Featured 90", 90, "600.00")
    p7 = _make_plan("Featured 7", 7, "80.00")
    p30_a = _make_plan("Featured 30 A", 30, "250.00")
    p30_b = _make_plan("Featured 30 B", 30, "260.00")

    ids = [row["id"] for row in APIClient().get(URL).json()]

    assert ids == [p7.id, p30_a.id, p30_b.id, p90.id]


def test_garbage_authorization_header_does_not_break_the_public_read():
    _make_plan("Featured 30", 30, "250.00")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")

    response = client.get(URL)

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_authenticated_user_can_read_too():
    _make_plan("Featured 30", 30, "250.00")
    user = User.objects.create_user(
        username="plans-reader@example.com",
        email="plans-reader@example.com",
        password="testpass123",
        account_type="business",
    )
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get(URL)

    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_write_methods_are_not_allowed(method):
    _make_plan("Featured 30", 30, "250.00")

    response = getattr(APIClient(), method)(URL, {"name": "x"}, format="json")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert Plan.objects.count() == 1


def test_list_uses_a_single_query(django_assert_num_queries):
    for days in (7, 30, 90):
        _make_plan(f"Featured {days}", days, "100.00")

    with django_assert_num_queries(1):
        response = APIClient().get(URL)

    assert response.status_code == 200
