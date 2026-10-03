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
