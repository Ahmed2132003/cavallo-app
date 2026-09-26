"""
Part P-064 STEP 2 — Search HTTP API tests.

The query/merge/cursor logic itself is already proven directly
against search/services.py in search/tests/test_services.py (STEP 1,
same convention feed/tests/test_get_home_feed.py established). This
file covers the HTTP layer: query-param parsing/validation, the
public (AllowAny) access rule, and this part's own explicit
acceptance criteria — most notably min_rating proven through a real
Rating submission (ratings.services.rate_business(), P-109), not a
hardcoded average_rating value, to prove the end-to-end chain from
P-109 through this endpoint.
"""

import itertools

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from businesses.models import BusinessProfile
from categories.models import Category
from products.models import Product
from ratings.services import rate_business

SEARCH_URL = reverse("search")

User = get_user_model()
_counter = itertools.count(1)


def make_business(name, *, country="Egypt", city="Cairo", business_type="trader", featured=False, category=None):
    email = f"search-api-{next(_counter)}@example.com"
    user = User.objects.create_user(
        username=email, email=email, password="Str0ngPass!23", account_type="business"
    )
    business = BusinessProfile.objects.create(
        user=user,
        business_name=name,
        business_type=business_type,
        country=country,
        city=city,
        category=category,
    )
    if featured:
        BusinessProfile.objects.filter(pk=business.pk).update(is_featured=True)
        business.refresh_from_db()
    return business


def make_customer():
    email = f"search-api-customer-{next(_counter)}@example.com"
    return User.objects.create_user(
        username=email, email=email, password="Str0ngPass!23", account_type="customer"
    )


def make_category(name="Fashion"):
    return Category.objects.create(name=name)


def make_product(business, category, name, *, price="100.00"):
    return Product.objects.create(
        business=business,
        category=category,
        name=name,
        description="A product.",
        price=price,
        currency=Product.CURRENCY_EGP,
    )


def _names(response_data):
    return {
        item.get("business_name") or item.get("name") for item in response_data["items"]
    }


def _result_types(response_data):
    return {item["result_type"] for item in response_data["items"]}


class TestSearchIsPublic(APITestCase):
    def test_unauthenticated_request_succeeds_with_empty_results(self):
        response = self.client.get(SEARCH_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"items": [], "next_cursor": None}


class TestFullTextQueryOnly(APITestCase):
    def test_query_alone_matches_relevant_items_across_both_content_types(self):
        cat = make_category()
        biz = make_business("Leather World")
        make_product(biz, cat, "Leather Bag")
        unrelated = make_business("Cotton House")
        make_product(unrelated, cat, "Cotton Shirt")

        response = self.client.get(SEARCH_URL, {"q": "leather"})

        assert response.status_code == status.HTTP_200_OK
        names = _names(response.data)
        assert "Leather World" in names
        assert "Leather Bag" in names
        assert "Cotton House" not in names
        assert "Cotton Shirt" not in names
        assert _result_types(response.data) == {"business", "product"}


class TestFilterOnlyNoQuery(APITestCase):
    def test_empty_q_with_filters_alone_returns_correctly_filtered_results(self):
        cat = make_category()
        cairo_biz = make_business("Cairo Traders", city="Cairo")
        make_product(cairo_biz, cat, "Cairo Product")
        alex_biz = make_business("Alex Traders", city="Alexandria")
        make_product(alex_biz, cat, "Alex Product")

        response = self.client.get(SEARCH_URL, {"city": "Cairo"})

        assert response.status_code == status.HTTP_200_OK
        names = _names(response.data)
        assert "Cairo Traders" in names
        assert "Cairo Product" in names
        assert "Alex Traders" not in names
        assert "Alex Product" not in names


class TestCombinedQueryAndFilters(APITestCase):
    def test_category_city_and_min_rating_narrow_correctly_where_applicable(self):
        """
        Acceptance criteria's own example: category=X&city=Cairo&
        min_rating=4. min_rating naturally applies to BusinessProfile
        only (Product carries no rating of its own) — so a matching
        product's business need NOT clear min_rating for that product
        to still appear; this is by design (search/services.py's own
        build_product_queryset() docstring), not a gap.
        """
        cat = make_category("Fashion")
        customer = make_customer()

        high_rated = make_business("High Rated Leather", city="Cairo", category=cat)
        rate_business(customer=customer, business=high_rated, score=5)
        make_product(high_rated, cat, "Leather Bag From High Rated")

        low_rated = make_business("Low Rated Leather", city="Cairo", category=cat)
        rate_business(customer=customer, business=low_rated, score=2)
        make_product(low_rated, cat, "Leather Bag From Low Rated")

        other_city = make_business("Leather Elsewhere", city="Alexandria", category=cat)
        rate_business(customer=customer, business=other_city, score=5)

        response = self.client.get(
            SEARCH_URL,
            {"q": "leather", "category": cat.id, "city": "Cairo", "min_rating": "4"},
        )

        assert response.status_code == status.HTTP_200_OK
        names = _names(response.data)
        # Business side: min_rating actually restricts businesses.
        assert "High Rated Leather" in names
        assert "Low Rated Leather" not in names
        assert "Leather Elsewhere" not in names
        # Product side: category+city apply, min_rating does not —
        # both products (from the high- and low-rated business alike)
        # are within scope since both match category+city.
        assert "Leather Bag From High Rated" in names
        assert "Leather Bag From Low Rated" in names


class TestMinRatingUsesRealRatingSubmission(APITestCase):
    def test_min_rating_filters_on_real_end_to_end_average_rating(self):
        customer_1 = make_customer()
        customer_2 = make_customer()

        high = make_business("High Rated Co.")
        rate_business(customer=customer_1, business=high, score=5)
        rate_business(customer=customer_2, business=high, score=4)  # average 4.5

        low = make_business("Low Rated Co.")
        rate_business(customer=customer_1, business=low, score=2)

        response = self.client.get(SEARCH_URL, {"min_rating": "4.0"})

        assert response.status_code == status.HTTP_200_OK
        names = _names(response.data)
        assert names == {"High Rated Co."}


class TestFeaturedOnly(APITestCase):
    def test_featured_only_restricts_both_content_types(self):
        cat = make_category()
        featured_biz = make_business("Featured Co.", featured=True)
        make_product(featured_biz, cat, "Featured Product")
        plain_biz = make_business("Plain Co.", featured=False)
        make_product(plain_biz, cat, "Plain Product")

        response = self.client.get(SEARCH_URL, {"featured_only": "true"})

        assert response.status_code == status.HTTP_200_OK
        assert _names(response.data) == {"Featured Co.", "Featured Product"}


class TestPriceFilterIsNonTransactional(APITestCase):
    _FORBIDDEN_SUBSTRINGS = ("buy", "cart", "checkout", "purchase", "order")

    def test_min_max_price_narrow_products_correctly_with_no_transactional_wording(self):
        """
        min_price/max_price apply to Product.price only (see
        build_business_queryset()'s own docstring — BusinessProfile
        has no price field, so it is never filtered by price). This
        test therefore asserts the narrowing on the product side only;
        the owning business legitimately still appears, unfiltered by
        price, which is correct behavior, not a bug.
        """
        cat = make_category()
        biz = make_business("Price Test Co.")
        make_product(biz, cat, "Cheap Item", price="10.00")
        make_product(biz, cat, "Mid Item", price="150.00")
        make_product(biz, cat, "Expensive Item", price="900.00")

        response = self.client.get(
            SEARCH_URL, {"min_price": "50.00", "max_price": "500.00"}
        )

        assert response.status_code == status.HTTP_200_OK
        product_names = {
            item["name"]
            for item in response.data["items"]
            if item["result_type"] == "product"
        }
        assert product_names == {"Mid Item"}

        body_lower = str(response.content).lower()
        for term in self._FORBIDDEN_SUBSTRINGS:
            assert term not in body_lower, f"found transactional term {term!r} in response"


class TestQueryParamValidation(APITestCase):
    def test_non_numeric_category_returns_400(self):
        response = self.client.get(SEARCH_URL, {"category": "not-a-number"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_non_numeric_min_rating_returns_400(self):
        response = self.client.get(SEARCH_URL, {"min_rating": "abc"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_non_numeric_min_price_returns_400(self):
        response = self.client.get(SEARCH_URL, {"min_price": "abc"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_featured_only_returns_400(self):
        response = self.client.get(SEARCH_URL, {"featured_only": "yes-please"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_malformed_cursor_returns_400(self):
        response = self.client.get(SEARCH_URL, {"cursor": "not-a-real-cursor!!"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_out_of_range_page_size_returns_400(self):
        response = self.client.get(SEARCH_URL, {"page_size": "999"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestPaginationNoGapsOrDuplicates(APITestCase):
    def test_cursor_resumes_across_pages_without_gaps_or_duplicates(self):
        cat = make_category()
        biz = make_business("Pagination Co.")
        for i in range(5):
            make_product(biz, cat, f"Paged Product {i}")

        collected = []
        cursor = None
        for _ in range(10):
            params = {"page_size": 2}
            if cursor:
                params["cursor"] = cursor
            response = self.client.get(SEARCH_URL, params)
            assert response.status_code == status.HTTP_200_OK
            if not response.data["items"]:
                break
            collected.extend(
                (item["result_type"], item.get("id")) for item in response.data["items"]
            )
            cursor = response.data["next_cursor"]
            if cursor is None:
                break

        # 1 business + 5 products = 6 total items, no duplicates.
        assert len(collected) == len(set(collected)) == 6