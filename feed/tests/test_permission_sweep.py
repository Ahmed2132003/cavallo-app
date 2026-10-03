"""
P-096 (step 2) permission sweep for the feed app (home + discover).

Both routes are authenticated GETs that derive everything from
request.user; there is no id parameter to attack.

Category 1: no token / garbage token -> 401 envelope on both routes.
Category 2: the feed (including the 90 s per-user cached first page)
            is never shared between users, and `user`/`user_id` query
            parameters are ignored.
Category 3: no capability-gated route exists in this app.
"""

import pytest
from django.core.cache import cache
from django.urls import reverse

from core.tests.sweep_factories import client_for, garbage_token_client
from core.tests.sweep_helpers import assert_unauthenticated
from feed.tests.helpers import make_business, make_customer, make_follow, make_post, ts

pytestmark = pytest.mark.django_db

ROUTES = ["home-feed", "discover-feed"]


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def _first_post_id(response):
    assert response.status_code == 200
    return response.json()["items"][0]["id"]


@pytest.mark.parametrize("client_factory", [client_for, garbage_token_client])
@pytest.mark.parametrize("route", ROUTES)
def test_unauthenticated_gets_401_envelope(route, client_factory):
    response = client_factory().get(reverse(route))

    assert_unauthenticated(response)


def test_home_feed_is_never_shared_between_users_even_when_cached():
    alice, bob = make_customer(), make_customer()
    biz_alice, biz_bob = make_business("Alice Biz"), make_business("Bob Biz")
    make_follow(alice, biz_alice)
    make_follow(bob, biz_bob)
    alice_post = make_post(biz_alice, at=ts(1))
    bob_post = make_post(biz_bob, at=ts(2))
    url = reverse("home-feed")

    first_alice = _first_post_id(client_for(alice).get(url))  # fills her cache
    first_bob = _first_post_id(client_for(bob).get(url))
    second_alice = _first_post_id(client_for(alice).get(url))  # served from cache

    assert first_alice == second_alice == alice_post.id
    assert first_bob == bob_post.id


def test_user_query_parameters_cannot_select_another_users_feed():
    alice, bob = make_customer(), make_customer()
    biz_alice, biz_bob = make_business("Alice Biz"), make_business("Bob Biz")
    make_follow(alice, biz_alice)
    make_follow(bob, biz_bob)
    alice_post = make_post(biz_alice, at=ts(1))
    make_post(biz_bob, at=ts(2))

    response = client_for(alice).get(
        reverse("home-feed"), {"user": bob.pk, "user_id": bob.pk}
    )

    assert _first_post_id(response) == alice_post.id


def test_discover_feed_never_includes_unpublished_content():
    viewer = make_customer()
    biz = make_business("Discover Biz")
    published = make_post(biz, at=ts(1))
    make_post(biz, at=ts(2), status="pending_review", caption="pending")
    make_post(biz, at=ts(3), status="rejected", caption="rejected")

    response = client_for(viewer).get(reverse("discover-feed"))

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [published.id]
