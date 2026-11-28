"""BE-10 - effective Featured = manual (Admin) OR ultra/infinity storage plan."""

import itertools
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone

from businesses.models import BusinessProfile
from businesses.serializers import BusinessProfileSerializer
from categories.models import Category
from content.models import Post
from content.serializers import PostPublicSerializer
from feed.cursor import CONTENT_TYPE_POST
from feed.serializers import FeedItemSerializer
from feed.services import FeedEntry
from products.models import Product
from products.serializers import ProductSerializer
from feed.services import get_discover_feed
from feed.tests.helpers import make_business as make_feed_business
from feed.tests.helpers import make_customer, make_post, ts
from monetization.featured import FEATURED_PLAN_CODES, sync_featured_effective
from monetization.models import FeaturedSubscription, Plan, Subscription
from monetization.services import activate_subscription, deactivate_subscriptions
from monetization.tasks import expire_subscriptions
from payments.storage_activation import activate_storage_subscription
from payments.tests.factories import make_business, make_storage_plan
from search.cursor import CONTENT_TYPE_BUSINESS
from search.services import SearchFilters, get_search_results

pytestmark = pytest.mark.django_db

GB = 1024**3
_SIZES = {"pro": 10, "max": 50, "ultra": 200}
_phones = itertools.count(70)


def _biz(name):
    return make_business(name, phone_number=f"+2010012345{next(_phones)}")


def _plan(code):
    if code == "infinity":
        return make_storage_plan(
            "infinity", price_egp=None, storage_gb=None, is_custom=True
        )
    return make_storage_plan(code, storage_gb=_SIZES[code])


def _manual_plan():
    return Plan.objects.create(
        name="Featured 30", duration_days=30, price=Decimal("250.00"), currency="EGP"
    )


def _sub(business, plan, **overrides):
    now = timezone.now()
    values = {
        "plan": plan,
        "status": "active",
        "started_at": now,
        "trial_ends_at": None,
        "current_period_start": now - timedelta(days=10),
        "current_period_end": now + timedelta(days=20),
        "quota_bytes": plan.storage_bytes or 20 * GB,
        "custom_gb": 20 if plan.is_custom else None,
        "purge_at": None,
        "pending_plan": None,
        "pending_quota_bytes": None,
        "is_featured_effective": plan.code in FEATURED_PLAN_CODES,
        "canceled_at": None,
    }
    values.update(overrides)
    sub, _ = Subscription.objects.update_or_create(business=business, defaults=values)
    return sub


def _flags(business):
    """(manual is_featured, is_featured_effective) straight from the DB."""
    return BusinessProfile.all_objects.values_list(
        "is_featured", "is_featured_effective"
    ).get(pk=business.pk)


def _end_period(sub):
    Subscription.objects.filter(pk=sub.pk).update(
        current_period_end=timezone.now() - timedelta(minutes=1)
    )
    expire_subscriptions()


def _buy(business, plan, gb=None):
    return activate_storage_subscription(business, plan, gb, SimpleNamespace(pk=1))


# ------------------------------------------------------------- plan rule


@pytest.mark.parametrize("code", ["ultra", "infinity"])
def test_ultra_and_infinity_make_the_business_featured(code):
    business = _biz("owner")
    _sub(business, _plan(code))
    assert _flags(business) == (False, True)


@pytest.mark.parametrize("code", ["pro", "max"])
def test_pro_and_max_never_feature_even_if_misconfigured(code):
    plan = _plan(code)
    Plan.objects.filter(pk=plan.pk).update(is_featured=True)
    plan.refresh_from_db()
    business = _biz("owner")
    _sub(business, plan, is_featured_effective=True)
    assert _flags(business) == (False, False)


def test_ultra_expiry_removes_featured():
    business = _biz("owner")
    sub = _sub(business, _plan("ultra"))

    _end_period(sub)

    sub.refresh_from_db()
    assert sub.status == "expired"
    assert _flags(business) == (False, False)


def test_upgrade_to_ultra_features_immediately():
    pro, ultra = _plan("pro"), _plan("ultra")
    business = _biz("owner")
    _sub(business, pro)
    assert _flags(business) == (False, False)

    _buy(business, ultra)

    assert _flags(business) == (False, True)


def test_pending_downgrade_keeps_featured_until_period_end():
    ultra, pro = _plan("ultra"), _plan("pro")
    business = _biz("owner")
    _sub(business, ultra)

    sub = _buy(business, pro)
    assert sub.pending_plan_id == pro.pk
    assert _flags(business) == (False, True)

    _end_period(sub)
    sub.refresh_from_db()
    assert sub.plan_id == pro.pk
    assert _flags(business) == (False, False)


def test_deleting_the_subscription_removes_plan_featured():
    business = _biz("owner")
    sub = _sub(business, _plan("ultra"))

    sub.delete()

    assert _flags(business) == (False, False)


# ---------------------------------------------------- manual (Admin) path


def test_manual_featured_survives_ultra_expiry():
    business = _biz("owner")
    sub = _sub(business, _plan("ultra"))
    activate_subscription(business, _manual_plan())

    _end_period(sub)

    assert _flags(business) == (True, True)


def test_manual_deactivation_keeps_plan_featured():
    business = _biz("owner")
    _sub(business, _plan("ultra"))
    activate_subscription(business, _manual_plan())

    deactivate_subscriptions(FeaturedSubscription.objects.filter(business=business))

    assert _flags(business) == (False, True)


def test_manual_only_featured_without_a_featured_plan():
    business = _biz("owner")
    activate_subscription(business, _manual_plan())
    assert _flags(business) == (True, True)

    deactivate_subscriptions(FeaturedSubscription.objects.filter(business=business))
    assert _flags(business) == (False, False)


def test_sync_is_idempotent():
    business = _biz("owner")
    _sub(business, _plan("ultra"))
    assert sync_featured_effective([business.pk]) == 0
    assert sync_featured_effective([]) == 0


# ------------------------------------------------- readers use effective


def test_public_serializer_reads_the_effective_flag():
    business = _biz("owner")
    _sub(business, _plan("ultra"))
    business.refresh_from_db()

    assert BusinessProfileSerializer(business).data["is_featured"] is True


def test_plan_featured_business_ranks_first_in_search():
    target = _biz("be10 target")
    plain = _biz("be10 plain")  # newer: wins on recency without Featured
    _sub(target, _plan("ultra"))

    result = get_search_results(SearchFilters(), q=None, page_size=20)

    businesses = [i for i in result["items"] if i.content_type == CONTENT_TYPE_BUSINESS]
    ids = [i.obj.pk for i in businesses]
    assert ids.index(target.pk) < ids.index(plain.pk)
    assert businesses[0].is_featured is True

    only = get_search_results(SearchFilters(featured_only=True), q=None, page_size=20)
    assert [i.obj.pk for i in only["items"] if i.content_type == CONTENT_TYPE_BUSINESS] == [
        target.pk
    ]


def test_plan_featured_business_ranks_first_in_discover_then_expiry_reverts():
    target = make_feed_business("BE10 Feed Target")
    plain = make_feed_business("BE10 Feed Plain")
    make_post(target, at=ts(1))
    make_post(plain, at=ts(10))
    sub = _sub(target, _plan("ultra"))
    viewer = make_customer()

    result = get_discover_feed(viewer, cursor=None, page_size=20)
    assert [e.obj.business_id for e in result["items"]] == [target.pk, plain.pk]
    assert result["items"][0].is_featured is True

    _end_period(sub)
    after = get_discover_feed(viewer, cursor=None, page_size=20)
    # Expired = locked: either hidden (BE-06) or shown, but never Featured.
    assert all(entry.is_featured is False for entry in after["items"])


# ------------------------------------------- badge on content payloads


def _fresh_post(pk):
    return Post.objects.select_related("business").get(pk=pk)


def test_post_and_feed_item_badge_follow_plan_featured():
    business = _biz("owner")
    sub = _sub(business, _plan("ultra"))
    post = Post.objects.create(business=business, caption="be10", status="published")

    fresh = _fresh_post(post.pk)
    assert PostPublicSerializer(fresh).data["is_featured"] is True
    item = FeedItemSerializer(FeedEntry(content_type=CONTENT_TYPE_POST, obj=fresh)).data
    assert item["is_featured"] is True

    _end_period(sub)
    assert PostPublicSerializer(_fresh_post(post.pk)).data["is_featured"] is False


@pytest.mark.parametrize("code,expected", [("ultra", True), ("pro", False)])
def test_product_badge_follows_plan(code, expected):
    business = _biz("owner")
    _sub(business, _plan(code))
    product = Product.objects.create(
        business=business,
        category=Category.objects.create(name=f"BE10 {code}"),
        name="BE10 Product",
        description="desc",
        price="10.00",
        currency=Product.CURRENCY_EGP,
    )
    fresh = Product.objects.select_related("business").get(pk=product.pk)

    assert ProductSerializer(fresh).data["is_featured"] is expected