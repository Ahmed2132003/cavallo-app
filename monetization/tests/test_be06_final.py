"""BE-06 final: 404 on detail GET, instant public-page hiding, real feed content."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from analytics.tests.bi02_helpers import make_post
from content.models import Post
from core.tests.sweep_factories import client_for, make_business, make_user
from core.tests.sweep_helpers import assert_error_envelope
from feed.services import get_discover_feed
from monetization.tests.helpers import give_subscription
from orders.tests.factories import make_product

pytestmark = pytest.mark.django_db


def _locked():
    business = make_business()
    give_subscription(business, "locked")
    return business


def _open():
    business = make_business()
    give_subscription(business, "trialing", trial_days=5)
    return business


def test_product_detail_is_404_for_a_locked_merchant():
    product = make_product(_locked())

    response = client_for().get(f"/api/v1/products/{product.pk}/")

    assert_error_envelope(response, 404, "NOT_FOUND")


def test_product_detail_is_visible_for_an_open_merchant():
    product = make_product(_open())

    assert client_for().get(f"/api/v1/products/{product.pk}/").status_code == 200


def test_post_detail_is_404_for_a_locked_merchant():
    post = make_post(_locked())

    response = client_for().get(f"/api/v1/posts/{post.pk}/")

    assert_error_envelope(response, 404, "NOT_FOUND")


def test_post_detail_is_visible_for_an_open_merchant():
    post = make_post(_open())

    assert client_for().get(f"/api/v1/posts/{post.pk}/").status_code == 200


def test_public_page_hides_the_moment_the_subscription_ends():
    cache.clear()
    business = make_business()
    give_subscription(business, "active", period_days=1)
    url = f"/api/v1/businesses/{business.pk}/"

    assert client_for().get(url).status_code == 200  # now cached

    # Time passes beyond the period end: no signal, no cache invalidation.
    later = timezone.now() + timedelta(days=2)
    with patch("django.utils.timezone.now", return_value=later):
        response = client_for().get(url)

    assert_error_envelope(response, 404, "NOT_FOUND")


def test_discover_feed_hides_locked_content_and_keeps_open_content():
    locked_post = make_post(_locked())
    open_post = make_post(_open())
    # Precondition: both are really visible to the feed without the gate.
    assert Post.published_objects.filter(pk=locked_post.pk).exists()
    assert Post.published_objects.filter(pk=open_post.pk).exists()

    items = get_discover_feed(make_user(), None, page_size=50)["items"]
    ids = {(e.content_type, e.id) for e in items}

    assert ("post", open_post.pk) in ids
    assert ("post", locked_post.pk) not in ids