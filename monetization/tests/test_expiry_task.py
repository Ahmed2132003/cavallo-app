"""
Part P-088 - tests for monetization.tasks.expire_featured_subscriptions.

The task selects FeaturedSubscription rows that are still is_active=True
but past expires_at and hands them to
monetization.services.deactivate_subscriptions(), which deactivates them
and recomputes BusinessProfile.is_featured in one transaction.

Covered:
  1. Normal expiry (deactivate + un-feature, history kept, boundary).
  2. The "defensive" overlapping-subscription concern. A literal business
     with TWO active rows cannot be built: the DB constraint
     uniq_active_featured_per_biz forbids it (pinned below). What CAN be
     tested, and is: a stale expired INACTIVE row never un-features a
     business that has a valid active subscription; the task goes through
     the recompute path instead of blindly clearing the flag; and the task
     source never writes the flags itself.
  3. Idempotency (re-runs, renewal after expiry).
  4. Beat registration (daily, 00:30 UTC).
  5. Integration with Feed and Search: expiry reverts the ranking.

Home Feed is tested through the service, not the view, because
HomeFeedView caches page 1 for 90s by design (P-060).
"""

import ast
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pytest
from celery.schedules import crontab
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

import monetization
from businesses.models import BusinessProfile
from categories.models import Category
from feed.services import get_discover_feed, get_home_feed
from feed.tests.helpers import make_business, make_customer, make_post, make_reel, ts
from monetization import services as monetization_services
from monetization import tasks as expiry_tasks
from monetization.models import FeaturedSubscription, Plan
from monetization.services import activate_subscription
from monetization.tasks import expire_featured_subscriptions
from products.models import Product
from search.cursor import CONTENT_TYPE_BUSINESS, CONTENT_TYPE_PRODUCT
from search.services import SearchFilters, get_search_results

pytestmark = pytest.mark.django_db


def _plan(name="Featured 30", days=30):
    return Plan.objects.create(
        name=name,
        duration_days=days,
        price=Decimal("250.00"),
        currency="EGP",
    )


def _backdate(subscription, hours=1):
    """Push expires_at into the past (the field is editable=False, so .update)."""
    FeaturedSubscription.objects.filter(pk=subscription.pk).update(
        expires_at=timezone.now() - timedelta(hours=hours)
    )


def _is_featured(business):
    return BusinessProfile.all_objects.get(pk=business.pk).is_featured


def _active_count(business):
    return FeaturedSubscription.objects.filter(
        business=business, is_active=True
    ).count()


# ---------------------------------------------------------------------------
# 1. Normal expiry
# ---------------------------------------------------------------------------


class TestNormalExpiry:
    def test_expired_subscription_is_deactivated_and_business_unfeatured(self):
        business = make_business("Expiring Co")
        sub = activate_subscription(business, _plan())
        assert _is_featured(business) is True
        _backdate(sub)

        result = expire_featured_subscriptions()

        sub.refresh_from_db()
        assert result == {"deactivated": 1}
        assert sub.is_active is False
        assert _is_featured(business) is False

    def test_expired_row_is_kept_for_history(self):
        business = make_business("History Co")
        sub = activate_subscription(business, _plan())
        _backdate(sub)
        expires_at_before = FeaturedSubscription.objects.get(pk=sub.pk).expires_at

        expire_featured_subscriptions()

        kept = FeaturedSubscription.objects.get(pk=sub.pk)
        assert kept.expires_at == expires_at_before
        assert FeaturedSubscription.objects.filter(business=business).count() == 1

    def test_unexpired_subscription_is_untouched(self):
        business = make_business("Valid Co")
        sub = activate_subscription(business, _plan())

        result = expire_featured_subscriptions()

        sub.refresh_from_db()
        assert result == {"deactivated": 0}
        assert sub.is_active is True
        assert _is_featured(business) is True

    def test_no_subscriptions_at_all_is_a_noop(self):
        make_business("Never Featured Co")

        assert expire_featured_subscriptions() == {"deactivated": 0}

    def test_expires_at_exactly_now_counts_as_expired(self):
        business = make_business("Boundary Co")
        sub = activate_subscription(business, _plan())
        frozen = timezone.now()
        FeaturedSubscription.objects.filter(pk=sub.pk).update(expires_at=frozen)

        with mock.patch.object(expiry_tasks.timezone, "now", return_value=frozen):
            result = expire_featured_subscriptions()

        assert result == {"deactivated": 1}
        assert _is_featured(business) is False

    def test_expires_one_second_in_the_future_is_not_expired(self):
        business = make_business("Almost Co")
        sub = activate_subscription(business, _plan())
        frozen = timezone.now()
        FeaturedSubscription.objects.filter(pk=sub.pk).update(
            expires_at=frozen + timedelta(seconds=1)
        )

        with mock.patch.object(expiry_tasks.timezone, "now", return_value=frozen):
            result = expire_featured_subscriptions()

        assert result == {"deactivated": 0}
        assert _is_featured(business) is True

    def test_only_the_expired_business_is_unfeatured(self):
        expired_business = make_business("Expired Co")
        valid_business = make_business("Still Valid Co")
        plan = _plan()
        expired_sub = activate_subscription(expired_business, plan)
        valid_sub = activate_subscription(valid_business, plan)
        _backdate(expired_sub)

        result = expire_featured_subscriptions()

        valid_sub.refresh_from_db()
        assert result == {"deactivated": 1}
        assert _is_featured(expired_business) is False
        assert valid_sub.is_active is True
        assert _is_featured(valid_business) is True

    def test_several_expired_subscriptions_in_one_run(self):
        plan = _plan()
        businesses = [make_business(f"Bulk {i}") for i in range(3)]
        for business in businesses:
            _backdate(activate_subscription(business, plan))

        result = expire_featured_subscriptions()

        assert result == {"deactivated": 3}
        assert all(_is_featured(business) is False for business in businesses)


# ---------------------------------------------------------------------------
# 2. Defensive correctness
# ---------------------------------------------------------------------------


class TestDefensiveCorrectness:
    def test_db_constraint_forbids_two_active_subscriptions_for_one_business(self):
        """
        Why a literal "business with two ACTIVE rows" test cannot be built:
        the DB itself refuses it. Pinned so nobody removes the constraint
        believing the expiry job makes it redundant.
        """
        business = make_business("Constraint Co")
        first = activate_subscription(business, _plan("Featured 7", 7))
        activate_subscription(business, _plan("Featured 90", 90))  # supersedes first

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                FeaturedSubscription.objects.filter(pk=first.pk).update(is_active=True)

    def test_stale_expired_inactive_row_does_not_unfeature_the_business(self):
        """
        The realistic overlap: an OLD superseded row (inactive, expires_at
        in the past) next to the CURRENT valid active row. Expiry must
        ignore the stale row and keep the business featured.
        """
        business = make_business("Renewed Co")
        old = activate_subscription(business, _plan("Featured 7", 7))
        current = activate_subscription(business, _plan("Featured 90", 90))
        _backdate(old)

        result = expire_featured_subscriptions()

        current.refresh_from_db()
        assert result == {"deactivated": 0}
        assert current.is_active is True
        assert _active_count(business) == 1
        assert _is_featured(business) is True

    def test_expiry_of_the_current_row_ignores_older_history_rows(self):
        business = make_business("Long History Co")
        plan = _plan()
        old = activate_subscription(business, plan)
        current = activate_subscription(business, plan)
        _backdate(old)
        _backdate(current)

        result = expire_featured_subscriptions()

        assert result == {"deactivated": 1}
        assert FeaturedSubscription.objects.filter(business=business).count() == 2
        assert _active_count(business) == 0
        assert _is_featured(business) is False

    def test_task_goes_through_the_recompute_path_not_a_blind_clear(self):
        """
        The flag must be RECOMPUTED from subscription state (which is what
        keeps a business featured if it still has an active row), not set
        to False blindly. Pinned by asserting the service's sync helper is
        what runs, with the affected business id.
        """
        business = make_business("Recompute Co")
        sub = activate_subscription(business, _plan())
        _backdate(sub)

        with mock.patch.object(
            monetization_services,
            "_sync_business_featured_flag",
            wraps=monetization_services._sync_business_featured_flag,
        ) as sync:
            expire_featured_subscriptions()

        sync.assert_called_once_with([business.pk])

    def test_tasks_module_never_writes_the_flags_directly(self):
        """
        tasks.py must not write is_featured or flip is_active itself; it
        must call deactivate_subscriptions() (P-087 rule).
        """
        source = Path(monetization.__file__).parent.joinpath("tasks.py").read_text(
            encoding="utf-8"
        )
        assert _writes_flags_directly(ast.parse(source)) is False

    def test_flag_write_detector_catches_direct_writes(self):
        assert _writes_flags_directly(ast.parse("qs.update(is_active=False)"))
        assert _writes_flags_directly(ast.parse("qs.update(is_featured=False)"))
        assert _writes_flags_directly(ast.parse("business.is_featured = False"))
        assert not _writes_flags_directly(
            ast.parse("qs.filter(is_active=True, expires_at__lte=now)")
        )


def _writes_flags_directly(tree):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and node.attr in {"is_featured", "is_active"}
        ):
            return True
        if isinstance(node, ast.keyword):
            if node.arg == "is_featured":
                return True
            if (
                node.arg == "is_active"
                and isinstance(node.value, ast.Constant)
                and node.value.value is False
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# 3. Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_running_twice_deactivates_once_and_does_not_error(self):
        business = make_business("Twice Co")
        sub = activate_subscription(business, _plan())
        _backdate(sub)

        first = expire_featured_subscriptions()
        second = expire_featured_subscriptions()

        sub.refresh_from_db()
        assert first == {"deactivated": 1}
        assert second == {"deactivated": 0}
        assert sub.is_active is False
        assert _is_featured(business) is False
        assert FeaturedSubscription.objects.filter(business=business).count() == 1

    def test_repeated_runs_leave_state_unchanged(self):
        business = make_business("Thrice Co")
        sub = activate_subscription(business, _plan())
        _backdate(sub)
        expire_featured_subscriptions()
        updated_at = FeaturedSubscription.objects.get(pk=sub.pk).updated_at

        for _ in range(3):
            assert expire_featured_subscriptions() == {"deactivated": 0}

        assert FeaturedSubscription.objects.get(pk=sub.pk).updated_at == updated_at
        assert _is_featured(business) is False

    def test_renewal_after_expiry_is_not_undone_by_a_later_run(self):
        business = make_business("Renewal Co")
        plan = _plan()
        old = activate_subscription(business, plan)
        _backdate(old)
        expire_featured_subscriptions()
        assert _is_featured(business) is False

        renewed = activate_subscription(business, plan)
        result = expire_featured_subscriptions()

        renewed.refresh_from_db()
        assert result == {"deactivated": 0}
        assert renewed.is_active is True
        assert _is_featured(business) is True


# ---------------------------------------------------------------------------
# 4. Beat registration
# ---------------------------------------------------------------------------


class TestBeatRegistration:
    def test_task_name(self):
        assert (
            expire_featured_subscriptions.name
            == "monetization.expire_featured_subscriptions"
        )

    def test_registered_daily_at_0030_utc(self):
        entry = settings.CELERY_BEAT_SCHEDULE["expire-featured-subscriptions"]
        assert entry["task"] == "monetization.expire_featured_subscriptions"
        assert entry["schedule"] == crontab(hour=0, minute=30)


# ---------------------------------------------------------------------------
# 5. Integration with Feed and Search: expiry reverts the ranking
# ---------------------------------------------------------------------------


def _feed_scenario():
    """target has the OLDER content, plain the NEWER; the viewer follows nobody."""
    target = make_business("Target Co")
    plain = make_business("Plain Co")
    make_post(target, at=ts(1))
    make_reel(target, at=ts(2))
    make_post(plain, at=ts(10))
    make_reel(plain, at=ts(11))
    return make_customer(), target, plain


def _feed_business_ids(result):
    return [entry.obj.business_id for entry in result["items"]]


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


def _names(result, content_type):
    return [
        item.obj.business_name
        if content_type == CONTENT_TYPE_BUSINESS
        else item.obj.name
        for item in result["items"]
        if item.content_type == content_type
    ]


class TestExpiryRevertsRanking:
    def test_home_feed_reverts_after_the_expiry_task(self):
        viewer, target, plain = _feed_scenario()
        sub = activate_subscription(target, _plan())
        assert _feed_business_ids(get_home_feed(viewer, None, 20))[0] == target.pk

        _backdate(sub)
        expire_featured_subscriptions()

        result = get_home_feed(viewer, cursor=None, page_size=20)
        assert _feed_business_ids(result) == [
            plain.pk,
            plain.pk,
            target.pk,
            target.pk,
        ]
        assert all(entry.is_featured is False for entry in result["items"])

    def test_discover_feed_reverts_after_the_expiry_task(self):
        viewer, target, plain = _feed_scenario()
        sub = activate_subscription(target, _plan())
        assert _feed_business_ids(get_discover_feed(viewer, None, 20))[:2] == [
            target.pk,
            target.pk,
        ]

        _backdate(sub)
        expire_featured_subscriptions()

        reverted = get_discover_feed(viewer, cursor=None, page_size=20)
        assert _feed_business_ids(reverted)[:2] == [plain.pk, plain.pk]

    def test_search_reverts_after_the_expiry_task(self):
        category = Category.objects.create(name="Fashion")
        target = make_business("Target Textiles")
        plain = make_business("Plain Textiles")
        _make_product(target, category, "Target Textiles Scarf", at=ts(1))
        _make_product(plain, category, "Plain Textiles Scarf", at=ts(10))
        sub = activate_subscription(target, _plan())
        boosted = get_search_results(SearchFilters(), q=None, page_size=20)
        assert _names(boosted, CONTENT_TYPE_BUSINESS)[0] == "Target Textiles"

        _backdate(sub)
        expire_featured_subscriptions()

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

    def test_featured_only_filter_drops_the_business_after_expiry(self):
        target = make_business("Filter Target")
        sub = activate_subscription(target, _plan())
        before = get_search_results(
            SearchFilters(featured_only=True), q=None, page_size=20
        )
        assert _names(before, CONTENT_TYPE_BUSINESS) == ["Filter Target"]

        _backdate(sub)
        expire_featured_subscriptions()

        after = get_search_results(
            SearchFilters(featured_only=True), q=None, page_size=20
        )
        assert _names(after, CONTENT_TYPE_BUSINESS) == []
