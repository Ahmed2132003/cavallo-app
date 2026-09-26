"""
Part P-064 STEP 1 — direct, no-HTTP tests for search/services.py,
matching feed/tests/test_get_home_feed.py's own convention of testing
the service function directly rather than through a view/client.
"""

import itertools
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from businesses.models import BusinessProfile
from categories.models import Category
from products.models import Product
from search.cursor import CONTENT_TYPE_BUSINESS, CONTENT_TYPE_PRODUCT, InvalidCursorError
from search.services import SearchFilters, get_search_results

pytestmark = pytest.mark.django_db

User = get_user_model()
_counter = itertools.count(1)
_BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def ts(minutes: int) -> datetime:
    return _BASE + timedelta(minutes=minutes)


def make_business(name, *, country="Egypt", city="Cairo", business_type="trader", featured=False, rating=None):
    email = f"search-{next(_counter)}@example.com"
    user = User.objects.create_user(
        username=email, email=email, password="Str0ngPass!23", account_type="business"
    )
    business = BusinessProfile.objects.create(
        user=user,
        business_name=name,
        business_type=business_type,
        country=country,
        city=city,
    )
    updates = {}
    if featured:
        updates["is_featured"] = True
    if rating is not None:
        updates["average_rating"] = Decimal(str(rating))
        updates["ratings_count"] = 1
    if updates:
        BusinessProfile.objects.filter(pk=business.pk).update(**updates)
        business.refresh_from_db()
    return business


def make_category(name="Fashion"):
    return Category.objects.create(name=name)


def make_product(business, category, name, *, price="100.00", at=None):
    product = Product.objects.create(
        business=business,
        category=category,
        name=name,
        description="A product.",
        price=price,
        currency=Product.CURRENCY_EGP,
    )
    if at is not None:
        Product.objects.filter(pk=product.pk).update(created_at=at)
        product.refresh_from_db()
    return product


class TestFilterOnlyNoQuery:
    def test_empty_query_with_filters_returns_matching_business_and_product(self):
        cat = make_category()
        biz = make_business("Cairo Leather Co.", city="Cairo")
        make_product(biz, cat, "Leather Bag", at=ts(1))
        other_biz = make_business("Alex Traders", city="Alexandria")
        make_product(other_biz, cat, "Alex Item", at=ts(2))

        result = get_search_results(
            SearchFilters(city="Cairo"), q=None, page_size=20
        )
        content_types = {r.content_type for r in result["items"]}
        names = {
            r.obj.business_name if r.content_type == CONTENT_TYPE_BUSINESS else r.obj.name
            for r in result["items"]
        }
        assert "Alex Item" not in names
        assert "Alex Traders" not in names
        assert "Cairo Leather Co." in names
        assert "Leather Bag" in names
        assert content_types == {CONTENT_TYPE_BUSINESS, CONTENT_TYPE_PRODUCT}


class TestFullTextOnly:
    def test_query_alone_matches_across_both_models(self):
        cat = make_category()
        biz = make_business("Leather World")
        make_product(biz, cat, "Leather Bag", at=ts(1))
        unrelated_biz = make_business("Cotton House")
        make_product(unrelated_biz, cat, "Cotton Shirt", at=ts(2))

        result = get_search_results(SearchFilters(), q="leather", page_size=20)
        names = {
            r.obj.business_name if r.content_type == CONTENT_TYPE_BUSINESS else r.obj.name
            for r in result["items"]
        }
        assert "Leather World" in names
        assert "Leather Bag" in names
        assert "Cotton House" not in names
        assert "Cotton Shirt" not in names


class TestCombinedQueryAndFilters:
    def test_query_plus_category_and_price_narrows_correctly(self):
        cat = make_category("Fashion")
        other_cat = make_category("Electronics")
        biz = make_business("Leather World")
        make_product(biz, cat, "Leather Bag", price="150.00", at=ts(1))
        make_product(biz, other_cat, "Leather Charger Case", price="50.00", at=ts(2))

        result = get_search_results(
            SearchFilters(category_id=cat.id, min_price=Decimal("100.00")),
            q="leather",
            page_size=20,
        )
        product_names = {
            r.obj.name for r in result["items"] if r.content_type == CONTENT_TYPE_PRODUCT
        }
        assert product_names == {"Leather Bag"}


class TestMinRatingUsesRealField:
    def test_min_rating_filters_on_business_average_rating(self):
        make_business("Low Rated", rating="3.00")
        high = make_business("High Rated", rating="4.50")

        result = get_search_results(SearchFilters(min_rating=Decimal("4.00")), q=None)
        business_names = {
            r.obj.business_name for r in result["items"] if r.content_type == CONTENT_TYPE_BUSINESS
        }
        assert business_names == {"High Rated"}
        assert high.average_rating == Decimal("4.50")


class TestFeaturedOnly:
    def test_featured_only_restricts_both_content_types(self):
        cat = make_category()
        featured_biz = make_business("Featured Co.", featured=True)
        make_product(featured_biz, cat, "Featured Product", at=ts(1))
        plain_biz = make_business("Plain Co.", featured=False)
        make_product(plain_biz, cat, "Plain Product", at=ts(2))

        result = get_search_results(SearchFilters(featured_only=True), q=None)
        names = {
            r.obj.business_name if r.content_type == CONTENT_TYPE_BUSINESS else r.obj.name
            for r in result["items"]
        }
        assert names == {"Featured Co.", "Featured Product"}


class TestIsFeaturedOrdering:
    def test_featured_items_sort_before_non_featured_regardless_of_recency(self):
        cat = make_category()
        plain_biz = make_business("Newer Plain", featured=False)
        make_product(plain_biz, cat, "Newer Plain Product", at=ts(10))
        featured_biz = make_business("Older Featured", featured=True)
        make_product(featured_biz, cat, "Older Featured Product", at=ts(1))

        result = get_search_results(SearchFilters(), q=None, page_size=20)
        ordered_names = [
            r.obj.business_name if r.content_type == CONTENT_TYPE_BUSINESS else r.obj.name
            for r in result["items"]
        ]
        assert ordered_names.index("Older Featured") < ordered_names.index("Newer Plain")
        assert ordered_names.index("Older Featured Product") < ordered_names.index(
            "Newer Plain Product"
        )


class TestPaginationAndCursor:
    def test_cursor_resumes_without_gaps_or_duplicates(self):
        cat = make_category()
        biz = make_business("Pagination Co.")
        for i in range(5):
            make_product(biz, cat, f"Product {i}", at=ts(i))

        page1 = get_search_results(SearchFilters(), q=None, page_size=3)
        assert len(page1["items"]) == 3
        assert page1["next_cursor"] is not None

        page2 = get_search_results(
            SearchFilters(), q=None, cursor=page1["next_cursor"], page_size=3
        )
        ids_page1 = {(r.content_type, r.id) for r in page1["items"]}
        ids_page2 = {(r.content_type, r.id) for r in page2["items"]}
        assert ids_page1.isdisjoint(ids_page2)
        # Business (created first, ts(0) baseline via auto_now_add) +
        # 5 products = 6 total items across both pages combined.
        assert len(ids_page1) + len(ids_page2) == 6

    def test_cursor_mode_mismatch_is_rejected(self):
        cat = make_category()
        biz = make_business("Mode Co.")
        make_product(biz, cat, "Mode Product", at=ts(1))

        recency_page = get_search_results(SearchFilters(), q=None, page_size=1)
        cursor = recency_page["next_cursor"]
        assert cursor is not None

        with pytest.raises(InvalidCursorError):
            get_search_results(SearchFilters(), q="mode", cursor=cursor, page_size=1)


class TestCountryCityJoinThroughBusiness:
    def test_product_country_city_filter_joins_through_owning_business(self):
        cat = make_category()
        cairo_biz = make_business("Cairo Biz", country="Egypt", city="Cairo")
        make_product(cairo_biz, cat, "Cairo Product", at=ts(1))
        riyadh_biz = make_business("Riyadh Biz", country="Saudi Arabia", city="Riyadh")
        make_product(riyadh_biz, cat, "Riyadh Product", at=ts(2))

        result = get_search_results(SearchFilters(country="Egypt", city="Cairo"), q=None)
        product_names = {
            r.obj.name for r in result["items"] if r.content_type == CONTENT_TYPE_PRODUCT
        }
        assert product_names == {"Cairo Product"}
        assert "Riyadh Product" not in product_names