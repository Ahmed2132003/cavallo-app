"""
P-096 (step 2) permission sweep for the ratings app.

Routes: POST /businesses/{id}/rate/ (authenticated upsert) and the
public GET /businesses/{id}/ratings/.

Category 1: no token / garbage token -> 401 envelope, no Rating row,
            the business aggregate untouched.
Category 2: the rating's author is always request.user (spoofed
            `customer`, `average_rating`, `ratings_count` in the body
            are ignored); user B rating never edits user A's row;
            unknown business -> 404 envelope.
Category 3: no capability-gated route exists in this app.

OPEN FINDING S-2 (characterised, NOT changed here): nothing forbids a
business owner from rating their OWN business, which lets an owner
inflate average_rating (used by Search's min_rating filter). The
architecture does not state a rule either way, so this needs a product
decision. The last test pins today's behaviour.
"""

from decimal import Decimal

import pytest

from core.tests.sweep_factories import (
    client_for,
    garbage_token_client,
    make_business,
    make_user,
)
from core.tests.sweep_helpers import assert_not_found, assert_unauthenticated
from ratings.models import Rating

pytestmark = pytest.mark.django_db


def _rate_url(pk):
    return f"/api/v1/businesses/{pk}/rate/"


@pytest.mark.parametrize("client_factory", [client_for, garbage_token_client])
def test_unauthenticated_gets_401_and_changes_nothing(client_factory):
    business = make_business()

    response = client_factory().post(_rate_url(business.pk), {"score": 5})

    assert_unauthenticated(response)
    assert Rating.objects.count() == 0
    business.refresh_from_db()
    assert business.ratings_count == 0
    assert business.average_rating == 0


def test_unknown_business_is_404_with_envelope_and_creates_nothing():
    response = client_for(make_user()).post(_rate_url(999999), {"score": 5})

    assert_not_found(response)
    assert Rating.objects.count() == 0


def test_spoofed_customer_and_aggregates_in_the_body_are_ignored():
    business = make_business()
    attacker, victim = make_user(), make_user()

    response = client_for(attacker).post(
        _rate_url(business.pk),
        {
            "score": 5,
            "customer": victim.pk,
            "average_rating": 1,
            "ratings_count": 99,
        },
        format="json",
    )

    assert response.status_code == 200
    rating = Rating.objects.get()
    assert rating.customer_id == attacker.pk
    business.refresh_from_db()
    assert business.ratings_count == 1
    assert business.average_rating == Decimal("5.00")


def test_another_users_rating_never_edits_my_row():
    business = make_business()
    me, other = make_user(), make_user()
    client_for(me).post(_rate_url(business.pk), {"score": 2}, format="json")

    client_for(other).post(
        _rate_url(business.pk),
        {"score": 5, "review_text": "other review"},
        format="json",
    )

    mine = Rating.objects.get(customer=me)
    assert mine.score == 2
    assert mine.review_text == ""
    assert Rating.objects.count() == 2
    business.refresh_from_db()
    assert business.ratings_count == 2
    assert business.average_rating == Decimal("3.50")


def test_public_ratings_list_stays_open_to_anonymous_users():
    business = make_business()

    response = client_for().get(f"/api/v1/businesses/{business.pk}/ratings/")

    assert response.status_code == 200


def test_finding_s2_owner_can_currently_rate_their_own_business():
    """Characterisation of open finding S-2 - see module docstring."""
    business = make_business()

    response = client_for(business.user).post(
        _rate_url(business.pk), {"score": 5}, format="json"
    )

    assert response.status_code == 200
    assert Rating.objects.get().customer_id == business.user_id
