"""
P-096 (step 2) permission sweep for the search app.

Search is public by design (AllowAny), so "401" does not apply; the
sweep instead proves the public surface leaks nothing it should not:

Category 1 (adapted): anonymous access is allowed and returns exactly
            what a logged-in user gets (no personalised data).
Category 2 (adapted): inactive and soft-deleted products never appear
            in results (with a positive control: the active product
            with the same keyword IS found), and hostile query strings
            never cause a 500.
Category 3: no capability-gated route exists in this app.
"""

import pytest
from django.urls import reverse

from core.tests.sweep_factories import client_for
from products.models import Product
from search.tests.test_api import (
    _names,
    make_business,
    make_category,
    make_customer,
    make_product,
)

pytestmark = pytest.mark.django_db

SEARCH_URL = reverse("search")


def test_anonymous_and_authenticated_users_get_identical_results():
    category = make_category()
    biz = make_business("Sweepium Traders")
    make_product(biz, category, "Sweepium Lamp")

    anonymous = client_for().get(SEARCH_URL, {"q": "sweepium"})
    logged_in = client_for(make_customer()).get(SEARCH_URL, {"q": "sweepium"})

    assert anonymous.status_code == logged_in.status_code == 200
    assert anonymous.json() == logged_in.json()
    assert "Sweepium Lamp" in _names(anonymous.json())


def test_inactive_and_soft_deleted_products_never_appear():
    category = make_category()
    biz = make_business("Keyword House")
    make_product(biz, category, "Keyworditem Active")
    inactive = make_product(biz, category, "Keyworditem Inactive")
    Product.objects.filter(pk=inactive.pk).update(is_active=False)
    deleted = make_product(biz, category, "Keyworditem Deleted")
    deleted.delete()  # soft delete

    response = client_for().get(SEARCH_URL, {"q": "keyworditem"})

    assert response.status_code == 200
    names = _names(response.json())
    assert "Keyworditem Active" in names  # positive control
    assert "Keyworditem Inactive" not in names
    assert "Keyworditem Deleted" not in names


@pytest.mark.parametrize(
    "hostile_q",
    [
        "'; DROP TABLE products_product; --",
        "<script>alert(1)</script>",
        "a" * 500,
    ],
    ids=["sql-injection", "script-tag", "very-long"],
)
def test_hostile_query_strings_never_cause_a_server_error(hostile_q):
    category = make_category()
    make_product(make_business("Safe Biz"), category, "Safe Product")

    response = client_for().get(SEARCH_URL, {"q": hostile_q})

    assert response.status_code == 200
    assert Product.objects.count() == 1
