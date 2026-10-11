"""
Regression tests for the "search shows nothing while typing" bug.

Before the fix, `q` was matched ONLY through the Postgres full-text
`search_vector` with a whole-word `SearchQuery`, so:

  * a partial word (what the user has typed so far, e.g. "leat") never
    matched "Leather", and
  * any row whose `search_vector` was NULL (created before the signal
    existed, imported in bulk, or edited through `QuerySet.update()`)
    could never be found with a query -- yet it was returned fine when
    `q` was empty, which is exactly the symptom reported.
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from businesses.models import BusinessProfile
from products.models import Product

from search.tests.test_api import (
    make_business,
    make_category,
    make_product,
    _names,
)

SEARCH_URL = reverse("search")


class TestPartialAndNullVectorMatching(APITestCase):
    def test_partial_word_matches_business_and_product(self):
        cat = make_category()
        biz = make_business("Leather World")
        make_product(biz, cat, "Leather Bag")

        response = self.client.get(SEARCH_URL, {"q": "leat"})

        assert response.status_code == status.HTTP_200_OK
        names = _names(response.data)
        assert "Leather World" in names
        assert "Leather Bag" in names

    def test_match_is_case_insensitive(self):
        biz = make_business("Leather World")

        response = self.client.get(SEARCH_URL, {"q": "LEATHER wor"})

        assert biz.business_name in _names(response.data)

    def test_rows_with_null_search_vector_are_still_found(self):
        cat = make_category()
        biz = make_business("Leather World")
        product = make_product(biz, cat, "Leather Bag")
        BusinessProfile.objects.filter(pk=biz.pk).update(search_vector=None)
        Product.objects.filter(pk=product.pk).update(search_vector=None)

        response = self.client.get(SEARCH_URL, {"q": "leather"})

        names = _names(response.data)
        assert "Leather World" in names
        assert "Leather Bag" in names

    def test_arabic_partial_word(self):
        cat = make_category()
        biz = make_business("متجر الجلود")
        make_product(biz, cat, "حذاء جلد طبيعي")

        response = self.client.get(SEARCH_URL, {"q": "حذ"})

        assert "حذاء جلد طبيعي" in _names(response.data)

    def test_unrelated_query_still_returns_nothing(self):
        cat = make_category()
        biz = make_business("Leather World")
        make_product(biz, cat, "Leather Bag")

        response = self.client.get(SEARCH_URL, {"q": "zzzzqqq"})

        assert response.data["items"] == []

    def test_partial_query_pagination_has_no_duplicates(self):
        cat = make_category()
        biz = make_business("Leather World")
        for i in range(7):
            make_product(biz, cat, f"Leather Item {i}")

        seen, cursor = [], None
        for _ in range(10):
            params = {"q": "leat", "page_size": 3}
            if cursor:
                params["cursor"] = cursor
            data = self.client.get(SEARCH_URL, params).data
            seen.extend((i["result_type"], i["id"]) for i in data["items"])
            cursor = data["next_cursor"]
            if not cursor:
                break

        assert len(seen) == len(set(seen)) == 8
