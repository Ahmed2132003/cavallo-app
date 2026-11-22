"""BE-01 tests: Plan catalog fields, byte constants, idempotent seed."""

import importlib
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from monetization import constants
from monetization.models import Plan

pytestmark = pytest.mark.django_db

URL = "/api/v1/monetization/plans/"
seed_migration = importlib.import_module("monetization.migrations.0002_plan_catalog")

MB = constants.BYTES_PER_MB
GB = constants.BYTES_PER_GB

# code -> (storage_bytes, price_egp, is_featured, trial_days, is_custom)
EXPECTED = {
    "free": (100 * MB, Decimal("0.00"), False, 15, False),
    "pro": (1 * GB, Decimal("99.00"), False, 0, False),
    "max": (3 * GB, Decimal("199.00"), False, 0, False),
    "ultra": (10 * GB, Decimal("449.00"), True, 0, False),
    "infinity": (None, None, False, 0, True),
}


@pytest.fixture(autouse=True)
def _clean_plans():
    Plan.objects.all().delete()


def _seed():
    seed_migration.seed_plans(django_apps, None)


def _legacy(name="Featured 30", days=30):
    return Plan.objects.create(
        name=name, duration_days=days, price=Decimal("250.00"), currency="EGP"
    )


def test_byte_constants():
    assert constants.BYTES_PER_KB == 1024
    assert constants.BYTES_PER_MB == 1024**2
    assert constants.BYTES_PER_GB == 1024**3
    assert constants.BYTES_PER_GB == 1024 * constants.BYTES_PER_MB


def test_legacy_plan_gets_safe_defaults():
    plan = _legacy()
    plan.refresh_from_db()
    assert plan.code is None
    assert plan.name_ar == "" and plan.name_en == ""
    assert plan.price_egp is None and plan.storage_bytes is None
    assert plan.is_featured is False
    assert plan.trial_days == 0
    assert plan.is_custom is False
    assert plan.is_active is True
    assert plan.sort_order == 0


def test_many_legacy_plans_with_null_code_are_allowed():
    _legacy("A", 7)
    _legacy("B", 30)
    assert Plan.objects.filter(code__isnull=True).count() == 2


def test_code_must_be_unique():
    _seed()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Plan.objects.create(
                code="pro", name="dup", duration_days=30,
                price=Decimal("1.00"), currency="EGP",
            )


def test_trial_days_allowed_only_for_free():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Plan.objects.create(
                code="pro", name="Pro", duration_days=30,
                price=Decimal("99.00"), currency="EGP", trial_days=15,
            )
    free = Plan.objects.create(
        code="free", name="Free", duration_days=30,
        price=Decimal("0.00"), currency="EGP", trial_days=15,
    )
    assert free.trial_days == 15


def test_seed_creates_exactly_the_five_plans():
    _seed()
    assert set(Plan.objects.values_list("code", flat=True)) == set(EXPECTED)


@pytest.mark.parametrize("code", list(EXPECTED))
def test_seed_values_match_spec(code):
    _seed()
    storage, price, featured, trial, custom = EXPECTED[code]
    plan = Plan.objects.get(code=code)
    assert plan.storage_bytes == storage
    assert plan.price_egp == price
    assert plan.is_featured is featured
    assert plan.trial_days == trial
    assert plan.is_custom is custom
    assert plan.is_active is True
    assert plan.name_ar and plan.name_en


def test_seed_sort_order_is_increasing_in_tier_order():
    _seed()
    codes = list(Plan.objects.order_by("sort_order").values_list("code", flat=True))
    assert codes == ["free", "pro", "max", "ultra", "infinity"]


def test_seed_is_idempotent():
    _seed()
    _seed()
    assert Plan.objects.count() == 5


def test_seed_does_not_overwrite_admin_edits():
    _seed()
    Plan.objects.filter(code="pro").update(price_egp=Decimal("120.00"), is_active=False)
    _seed()
    pro = Plan.objects.get(code="pro")
    assert pro.price_egp == Decimal("120.00")
    assert pro.is_active is False


def test_seed_keeps_existing_featured_plans_untouched():
    legacy = _legacy()
    _seed()
    legacy.refresh_from_db()
    assert legacy.code is None
    assert legacy.duration_days == 30
    assert legacy.price == Decimal("250.00")
    assert Plan.objects.count() == 6


def test_api_returns_catalog_in_tier_order_with_new_fields():
    _seed()
    rows = APIClient().get(URL).json()

    assert [r["code"] for r in rows] == ["free", "pro", "max", "ultra", "infinity"]
    free, ultra, infinity = rows[0], rows[3], rows[4]
    assert free["storage_bytes"] == 100 * MB
    assert free["price_egp"] == "0.00"
    assert free["trial_days"] == 15
    assert ultra["is_featured"] is True
    assert ultra["storage_bytes"] == 10 * GB
    assert infinity["is_custom"] is True
    assert infinity["price_egp"] is None
    assert infinity["storage_bytes"] is None
    # legacy keys still present
    for key in ("id", "name", "duration_days", "price", "currency"):
        assert key in free