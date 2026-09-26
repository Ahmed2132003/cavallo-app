"""
Part P-063 (Phase 11, ADR-003) - Product.search_vector /
BusinessProfile.search_vector signal + GIN index tests.
"""

import pytest
from django.contrib.postgres.search import SearchQuery
from django.db import connection

from accounts.models import User
from businesses.models import BusinessProfile
from categories.models import Category
from products.models import Product

pytestmark = pytest.mark.django_db


def _make_business(email: str, **overrides) -> BusinessProfile:
    user = User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type="business",
    )
    defaults = {
        "user": user,
        "business_name": "Acme Trading",
        "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
        "country": "Egypt",
        "city": "Cairo",
        "description": "",
    }
    defaults.update(overrides)
    return BusinessProfile.objects.create(**defaults)


def _make_category(name: str = "Fashion") -> Category:
    return Category.objects.create(name=name)


def _make_product(business=None, category=None, **overrides) -> Product:
    defaults = {
        "business": business or _make_business("p063-owner@example.com"),
        "category": category or _make_category(),
        "name": "Classic Shirt",
        "description": "A shirt.",
        "price": "199.99",
        "currency": Product.CURRENCY_EGP,
    }
    defaults.update(overrides)
    return Product.objects.create(**defaults)


def _index_exists(index_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_indexes WHERE indexname = %s", [index_name])
        return cursor.fetchone() is not None


class TestProductSearchVectorSignal:
    def test_create_populates_search_vector_and_matches_relevant_term(self):
        product = _make_product(
            name="Leather Jacket", description="Genuine cowhide, tailored fit."
        )
        product.refresh_from_db()
        assert product.search_vector is not None

        matches = Product.objects.filter(
            pk=product.pk, search_vector=SearchQuery("leather")
        )
        assert matches.exists()

    def test_create_excludes_irrelevant_term(self):
        product = _make_product(
            name="Leather Jacket", description="Genuine cowhide, tailored fit."
        )
        non_matches = Product.objects.filter(
            pk=product.pk, search_vector=SearchQuery("sunglasses")
        )
        assert not non_matches.exists()

    def test_update_refreshes_search_vector_old_term_gone_new_term_present(self):
        product = _make_product(name="Leather Jacket", description="Old text.")

        product.name = "Cotton Hoodie"
        product.description = "Soft and warm."
        product.save()

        old_term_matches = Product.objects.filter(
            pk=product.pk, search_vector=SearchQuery("leather")
        )
        new_term_matches = Product.objects.filter(
            pk=product.pk, search_vector=SearchQuery("hoodie")
        )
        assert not old_term_matches.exists()
        assert new_term_matches.exists()

    def test_repeated_saves_do_not_hang_or_error_no_signal_recursion(self):
        product = _make_product(name="Leather Jacket", description="Old text.")
        for i in range(5):
            product.description = f"Revision {i}."
            product.save()
        product.refresh_from_db()
        assert product.search_vector is not None

    def test_gin_index_exists(self):
        assert _index_exists("products_search_vector_gin")


class TestBusinessProfileSearchVectorSignal:
    def test_create_populates_search_vector_and_matches_relevant_term(self):
        business = _make_business(
            "p063-biz1@example.com",
            business_name="Nile Textiles",
            description="Wholesale fabric supplier.",
        )
        matches = BusinessProfile.objects.filter(
            pk=business.pk, search_vector=SearchQuery("textiles")
        )
        assert matches.exists()

    def test_create_excludes_irrelevant_term(self):
        business = _make_business(
            "p063-biz2@example.com",
            business_name="Nile Textiles",
            description="Wholesale fabric supplier.",
        )
        non_matches = BusinessProfile.objects.filter(
            pk=business.pk, search_vector=SearchQuery("sunglasses")
        )
        assert not non_matches.exists()

    def test_update_refreshes_search_vector_old_term_gone_new_term_present(self):
        business = _make_business(
            "p063-biz3@example.com",
            business_name="Nile Textiles",
            description="Old text.",
        )

        business.business_name = "Delta Furniture"
        business.description = "Handmade wooden furniture."
        business.save()

        old_term_matches = BusinessProfile.objects.filter(
            pk=business.pk, search_vector=SearchQuery("textiles")
        )
        new_term_matches = BusinessProfile.objects.filter(
            pk=business.pk, search_vector=SearchQuery("furniture")
        )
        assert not old_term_matches.exists()
        assert new_term_matches.exists()

    def test_gin_index_exists(self):
        assert _index_exists("business_search_vector_gin")