"""
Part P-087 - integration tests: real FeaturedSubscription state -> real
Feed / Search ordering.

Nothing here sets BusinessProfile.is_featured by hand. Every state change
goes through the sanctioned services:

    activate_subscription()      (what the Admin action / P-090 webhook call)
    deactivate_subscriptions()   (what the Admin action / P-088 expiry call)

Three claims are proven:
  1. Activation ranks that business's content higher; expiry reverts it.
  2. Architecture Section 21: Featured is a priority BOOST. Non-featured
     content is never excluded - only reordered - unless the caller
     explicitly opts in with featured_only=True.
  3. Post / Reel / Product carry no is_featured column of their own; the
     status is resolved through the owning business at query time.

The services are tested directly (not through the cached Home Feed view):
HomeFeedView caches page 1 for 90s by design (P-060), so an HTTP test
would observe cache staleness, not ranking.
"""

from decimal import Decimal

import pytest

from categories.models import Category
from content.models import Post, Reel
from feed.services import get_discover_feed, get_home_feed
from feed.tests.helpers import make_business, make_customer, make_post, make_reel, ts
from monetization.models import FeaturedSubscription, Plan
from monetization.services import activate_subscription, deactivate_subscriptions
from products.models import Product
from search.cursor import CONTENT_TYPE_BUSINESS, CONTENT_TYPE_PRODUCT
from search.services import SearchFilters, get_search_results

pytestmark = pytest.mark.django_db


def _plan():
    return Plan.objects.create(
        name="Featured 30",
        duration_days=30,
        price=Decimal("250.00"),
        currency="EGP",
    )


def _expire(business):
    """Simulate the P-088 expiry job: the sanctioned deactivation path."""
    return deactivate_subscriptions(
        FeaturedSubscription.objects.filter(business=business, is_active=True)
    )


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------


def _feed_scenario():
    """
    `target` (becomes Featured) has the OLDER content; `plain` has the
    NEWER content. Without Featured, plain must win on recency. The
    viewer follows nobody, so everything comes from the backfill tier.
    """
    target = make_business("Target Co")
    plain = make_business("Plain Co")
    make_post(target, at=ts(1))
    make_reel(target, at=ts(2))
    make_post(plain, at=ts(10))
    make_reel(plain, at=ts(11))
    return make_customer(), target, plain


def _feed_business_ids(result):
    return [entry.obj.business_id for entry in result["items"]]


def _walk_home_feed(user, page_size):
    """Follow next_cursor to the end; return business ids in served order."""
    served = []
    cursor = None
    for _ in range(10):
        result = get_home_feed(user, cursor=cursor, page_size=page_size)
        served.extend(_feed_business_ids(result))
        cursor = result["next_cursor"]
        if cursor is None:
            return served
    raise AssertionError("feed pagination did not terminate")


class TestFeedFollowsSubscriptionState:
    def test_without_a_subscription_the_feed_is_plain_recency(self):
        viewer, target, plain = _feed_scenario()

        result = get_home_feed(viewer, cursor=None, page_size=20)

        assert _feed_business_ids(result) == [
            plain.pk,
            plain.pk,
            target.pk,
            target.pk,
        ]
        assert all(entry.is_featured is False for entry in result["items"])

    def test_activation_ranks_older_featured_content_ahead_of_newer_plain(self):
        viewer, target, plain = _feed_scenario()

        activate_subscription(target, _plan())

        result = get_home_feed(viewer, cursor=None, page_size=20)
        assert _feed_business_ids(result) == [
            target.pk,
            target.pk,
            plain.pk,
            plain.pk,
        ]
        assert [entry.is_featured for entry in result["items"]] == [
            True,
            True,
            False,
            False,
        ]

    def test_activation_wins_when_content_is_equally_recent(self):
        target = make_business("Target Co")  # created first -> lower id
        plain = make_business("Plain Co")
        make_post(target, at=ts(5))
        make_post(plain, at=ts(5))
        viewer = make_customer()

        activate_subscription(target, _plan())

        result = get_home_feed(viewer, cursor=None, page_size=20)
        assert _feed_business_ids(result)[0] == target.pk

    def test_expiry_reverts_the_ranking(self):
        viewer, target, plain = _feed_scenario()
        activate_subscription(target, _plan())
        assert _feed_business_ids(get_home_feed(viewer, None, 20))[0] == target.pk

        _expire(target)

        result = get_home_feed(viewer, cursor=None, page_size=20)
        assert _feed_business_ids(result) == [
            plain.pk,
            plain.pk,
            target.pk,
            target.pk,
        ]
        assert all(entry.is_featured is False for entry in result["items"])

    def test_discover_feed_follows_subscription_state_too(self):
        viewer, target, plain = _feed_scenario()

        activate_subscription(target, _plan())
        featured = get_discover_feed(viewer, cursor=None, page_size=20)
        assert _feed_business_ids(featured)[:2] == [target.pk, target.pk]

        _expire(target)
        reverted = get_discover_feed(viewer, cursor=None, page_size=20)
        assert _feed_business_ids(reverted)[:2] == [plain.pk, plain.pk]


class TestFeedFeaturedIsABoostNotAnExclusion:
    def test_non_featured_content_is_still_served(self):
        viewer, target, plain = _feed_scenario()
        activate_subscription(target, _plan())

        result = get_home_feed(viewer, cursor=None, page_size=20)

        assert len(result["items"]) == 4
        assert plain.pk in _feed_business_ids(result)

    def test_every_item_is_reachable_by_paging_to_the_end(self):
        """Paging with a small page size must still surface ALL content."""
        viewer, target, plain = _feed_scenario()
        activate_subscription(target, _plan())

        served = _walk_home_feed(viewer, page_size=2)

        assert served == [target.pk, target.pk, plain.pk, plain.pk]

    def test_featured_content_does_not_hide_a_followed_business(self):
        """Following tier is untouched by Featured: a followed plain
        business still comes first for the follower."""
        from feed.tests.helpers import make_follow

        viewer, target, plain = _feed_scenario()
        make_follow(viewer, plain)
        activate_subscription(target, _plan())

        result = get_home_feed(viewer, cursor=None, page_size=20)

        assert _feed_business_ids(result)[:2] == [plain.pk, plain.pk]
        assert len(result["items"]) == 4


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _make_product(business, category, name, *, at):
    product = Product.objects.create(
        business=business,
        category=category,
        name=name,
        description="A product.",
        price="100.00",
        currency=Product.CURRENCY_EGP,
    )
    Product.objects.filter(pk=product.pk).update(created_at=at)
    return product


def _search_scenario():
    """target is created FIRST (older business) with the older product."""
    category = Category.objects.create(name="Fashion")
    target = make_business("Target Textiles")
    plain = make_business("Plain Textiles")
    _make_product(target, category, "Target Textiles Scarf", at=ts(1))
    _make_product(plain, category, "Plain Textiles Scarf", at=ts(10))
    return target, plain


def _names(result, content_type):
    return [
        item.obj.business_name
        if content_type == CONTENT_TYPE_BUSINESS
        else item.obj.name
        for item in result["items"]
        if item.content_type == content_type
    ]


class TestSearchFollowsSubscriptionState:
    def test_without_a_subscription_search_is_plain_recency(self):
        _search_scenario()

        result = get_search_results(SearchFilters(), q=None, page_size=20)

        assert _names(result, CONTENT_TYPE_BUSINESS) == [
            "Plain Textiles",
            "Target Textiles",
        ]
        assert _names(result, CONTENT_TYPE_PRODUCT) == [
            "Plain Textiles Scarf",
            "Target Textiles Scarf",
        ]

    def test_activation_boosts_business_and_products_in_recency_mode(self):
        target, _plain = _search_scenario()

        activate_subscription(target, _plan())

        result = get_search_results(SearchFilters(), q=None, page_size=20)
        assert _names(result, CONTENT_TYPE_BUSINESS) == [
            "Target Textiles",
            "Plain Textiles",
        ]
        assert _names(result, CONTENT_TYPE_PRODUCT) == [
            "Target Textiles Scarf",
            "Plain Textiles Scarf",
        ]
        flags = {item.is_featured for item in result["items"][:2]}
        assert flags == {True}

    def test_activation_boosts_in_relevance_mode(self):
        target, _plain = _search_scenario()

        activate_subscription(target, _plan())

        result = get_search_results(SearchFilters(), q="Textiles", page_size=20)
        assert _names(result, CONTENT_TYPE_BUSINESS)[0] == "Target Textiles"
        assert _names(result, CONTENT_TYPE_PRODUCT)[0] == "Target Textiles Scarf"

    def test_expiry_reverts_the_ranking(self):
        target, _plain = _search_scenario()
        activate_subscription(target, _plan())

        _expire(target)

        result = get_search_results(SearchFilters(), q=None, page_size=20)
        assert _names(result, CONTENT_TYPE_BUSINESS) == [
            "Plain Textiles",
            "Target Textiles",
        ]
        assert _names(result, CONTENT_TYPE_PRODUCT) == [
            "Plain Textiles Scarf",
            "Target Textiles Scarf",
        ]
        assert all(item.is_featured is False for item in result["items"])


class TestSearchFeaturedIsABoostNotAnExclusion:
    def test_default_filters_still_return_non_featured_results(self):
        """The Section 21 proof: featured_only defaults to False, so
        non-featured results appear, just ranked lower."""
        target, _plain = _search_scenario()
        activate_subscription(target, _plan())

        assert SearchFilters().featured_only is False
        for q in (None, "Textiles"):
            result = get_search_results(SearchFilters(), q=q, page_size=20)
            assert set(_names(result, CONTENT_TYPE_BUSINESS)) == {
                "Target Textiles",
                "Plain Textiles",
            }
            assert set(_names(result, CONTENT_TYPE_PRODUCT)) == {
                "Target Textiles Scarf",
                "Plain Textiles Scarf",
            }

    def test_only_an_explicit_featured_only_opt_in_restricts_results(self):
        target, _plain = _search_scenario()
        activate_subscription(target, _plan())

        result = get_search_results(
            SearchFilters(featured_only=True), q=None, page_size=20
        )

        assert _names(result, CONTENT_TYPE_BUSINESS) == ["Target Textiles"]
        assert _names(result, CONTENT_TYPE_PRODUCT) == ["Target Textiles Scarf"]

    def test_every_result_is_reachable_by_paging_to_the_end(self):
        target, _plain = _search_scenario()
        activate_subscription(target, _plan())

        seen = []
        cursor = None
        for _ in range(10):
            result = get_search_results(
                SearchFilters(), q=None, cursor=cursor, page_size=2
            )
            seen.extend((item.content_type, item.id) for item in result["items"])
            cursor = result["next_cursor"]
            if cursor is None:
                break
        else:
            raise AssertionError("search pagination did not terminate")

        assert len(seen) == 4
        assert len(set(seen)) == 4


# ---------------------------------------------------------------------------
# Design: no duplicated denormalised field on content models
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", [Post, Reel, Product])
def test_content_models_do_not_duplicate_is_featured(model):
    """Featured status is resolved via business__is_featured at query
    time; Post/Reel/Product must not grow their own copy."""
    assert "is_featured" not in {field.name for field in model._meta.get_fields()}