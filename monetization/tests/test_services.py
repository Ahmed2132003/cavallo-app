"""Service tests for monetization.services.activate_subscription (Part P-086)."""

import ast
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pytest
from django.contrib.auth import get_user_model

import monetization
from businesses.services import create_business_profile
from monetization.models import FeaturedSubscription, Plan
from monetization.services import activate_subscription

User = get_user_model()


def _make_business(suffix="a"):
    email = f"monetization-service-{suffix}@example.com"
    user = User.objects.create_user(
        username=email,
        email=email,
        password="testpass123",
        account_type="business",
    )
    return create_business_profile(
        user=user,
        business_name=f"Monetization Service Trader {suffix}",
        business_type="trader",
        country="EG",
        city="Ismailia",
    )


def _make_plan(name="Featured 30", days=30):
    return Plan.objects.create(
        name=name,
        duration_days=days,
        price=Decimal("250.00"),
        currency="EGP",
    )


def _active(business):
    return FeaturedSubscription.objects.filter(business=business, is_active=True)


@pytest.mark.django_db
class TestActivateSubscription:
    def test_first_activation_creates_one_active_row(self):
        business = _make_business()
        plan = _make_plan(days=30)

        sub = activate_subscription(business, plan)

        assert isinstance(sub, FeaturedSubscription)
        assert sub.pk is not None
        assert sub.business_id == business.pk
        assert sub.plan_id == plan.pk
        assert sub.is_active is True
        assert sub.expires_at == sub.starts_at + timedelta(days=30)
        assert FeaturedSubscription.objects.filter(business=business).count() == 1
        assert _active(business).count() == 1

    def test_second_activation_supersedes_the_first(self):
        """The critical case: never two active subscriptions for one business."""
        business = _make_business()
        first = activate_subscription(business, _make_plan("Featured 7", 7))
        first_expires_at = first.expires_at

        second = activate_subscription(business, _make_plan("Featured 90", 90))

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.pk != second.pk
        assert first.is_active is False
        assert second.is_active is True
        # History is kept: the old row still exists, only its flag changed.
        assert FeaturedSubscription.objects.filter(pk=first.pk).exists()
        assert first.expires_at == first_expires_at
        assert FeaturedSubscription.objects.filter(business=business).count() == 2
        active = _active(business)
        assert active.count() == 1
        assert active.get().pk == second.pk

    def test_three_sequential_activations_leave_exactly_one_active(self):
        business = _make_business()
        plan = _make_plan()

        subs = [activate_subscription(business, plan) for _ in range(3)]

        assert FeaturedSubscription.objects.filter(business=business).count() == 3
        assert _active(business).count() == 1
        assert _active(business).get().pk == subs[-1].pk

    def test_other_businesses_are_not_affected(self):
        business_a = _make_business("a")
        business_b = _make_business("b")
        plan = _make_plan()
        sub_b = activate_subscription(business_b, plan)

        activate_subscription(business_a, plan)
        activate_subscription(business_a, plan)

        sub_b.refresh_from_db()
        assert sub_b.is_active is True
        assert _active(business_a).count() == 1
        assert _active(business_b).count() == 1
        assert FeaturedSubscription.objects.filter(is_active=True).count() == 2

    def test_expires_at_uses_the_new_plans_duration(self):
        business = _make_business()
        activate_subscription(business, _make_plan("Featured 7", 7))

        second = activate_subscription(business, _make_plan("Featured 90", 90))

        assert second.expires_at - second.starts_at == timedelta(days=90)
        second.refresh_from_db()
        assert second.expires_at - second.starts_at == timedelta(days=90)

    def test_reactivating_the_same_plan_creates_a_new_row(self):
        business = _make_business()
        plan = _make_plan()
        first = activate_subscription(business, plan)

        second = activate_subscription(business, plan)

        first.refresh_from_db()
        assert second.pk != first.pk
        assert first.is_active is False
        assert second.is_active is True

    def test_failure_rolls_back_the_deactivation(self):
        """Atomicity: if creating the new row fails, the old one stays active."""
        business = _make_business()
        plan = _make_plan()
        first = activate_subscription(business, plan)

        with mock.patch.object(
            FeaturedSubscription.objects, "create", side_effect=RuntimeError("boom")
        ):
            with pytest.raises(RuntimeError):
                activate_subscription(business, plan)

        first.refresh_from_db()
        assert first.is_active is True
        assert _active(business).count() == 1

    def test_does_not_touch_business_is_featured(self):
        """Wiring BusinessProfile.is_featured to this state is Part P-087."""
        business = _make_business()

        activate_subscription(business, _make_plan())

        business.refresh_from_db()
        assert business.is_featured is False


_WRITE_CALLS = {"create", "get_or_create", "update_or_create", "update", "bulk_create"}


def _sets_active_true(tree):
    """
    True if the parsed module activates a FeaturedSubscription directly:
    either `x.is_active = True`, or is_active=True passed to a write call
    (create/update/...) or to the FeaturedSubscription constructor.
    Read-side uses (filter(is_active=True), Q(is_active=True)) do not count.
    """
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and node.value.value is True
            and any(
                isinstance(target, ast.Attribute) and target.attr == "is_active"
                for target in node.targets
            )
        ):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _WRITE_CALLS or name == "FeaturedSubscription":
                for keyword in node.keywords:
                    if (
                        keyword.arg == "is_active"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ):
                        return True
    return False


def test_guard_detects_direct_activation():
    """The architecture guard below must not be vacuous."""
    assert _sets_active_true(
        ast.parse("FeaturedSubscription.objects.create(is_active=True)")
    )
    assert _sets_active_true(ast.parse("sub.is_active = True"))
    assert _sets_active_true(ast.parse("qs.update(is_active=True)"))
    assert not _sets_active_true(ast.parse("qs.filter(is_active=True)"))
    assert not _sets_active_true(ast.parse("qs.update(is_active=False)"))


def test_only_services_module_sets_is_active_true():
    """
    Architecture rule: activate_subscription() is the ONLY code path that
    makes a FeaturedSubscription active. Within the monetization package
    (outside tests/migrations) only services.py may do that.
    """
    package_dir = Path(monetization.__file__).parent
    offenders = sorted(
        path.name
        for path in package_dir.glob("*.py")
        if _sets_active_true(ast.parse(path.read_text(encoding="utf-8")))
    )
    assert offenders == ["services.py"]
