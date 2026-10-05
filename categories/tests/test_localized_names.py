"""Localized category names (Part P-112)."""

import importlib

import pytest
from django.apps import apps as global_apps
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient

from categories.models import Category
from categories.views import CATEGORY_TREE_CACHE_KEYS

pytestmark = pytest.mark.django_db

ARABIC_FASHION = "\u0623\u0632\u064a\u0627\u0621"
ARABIC_MEN = "\u0631\u062c\u0627\u0644\u064a"


@pytest.fixture(autouse=True)
def _clear_tree_caches():
    cache.delete_many(CATEGORY_TREE_CACHE_KEYS)
    yield
    cache.delete_many(CATEGORY_TREE_CACHE_KEYS)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def tree_url():
    return reverse("categories:category-tree")


def _make_fashion_tree():
    fashion = Category.objects.create(
        name="Fashion", name_en="Fashion", name_ar=ARABIC_FASHION
    )
    Category.objects.create(
        name="Men", name_en="Men", name_ar=ARABIC_MEN, parent=fashion
    )
    return fashion


def test_arabic_header_returns_arabic_names(api_client, tree_url):
    _make_fashion_tree()

    body = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar").json()

    assert body[0]["name"] == ARABIC_FASHION
    assert body[0]["children"][0]["name"] == ARABIC_MEN


def test_english_header_returns_english_names(api_client, tree_url):
    _make_fashion_tree()

    body = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="en").json()

    assert body[0]["name"] == "Fashion"
    assert body[0]["children"][0]["name"] == "Men"


def test_missing_header_falls_back_to_english(api_client, tree_url):
    _make_fashion_tree()

    body = api_client.get(tree_url).json()

    assert body[0]["name"] == "Fashion"


def test_region_and_quality_in_header_are_understood(api_client, tree_url):
    _make_fashion_tree()

    body = api_client.get(
        tree_url, HTTP_ACCEPT_LANGUAGE="ar-EG,ar;q=0.9,en;q=0.8"
    ).json()

    assert body[0]["name"] == ARABIC_FASHION


def test_unsupported_language_falls_back_to_english(api_client, tree_url):
    _make_fashion_tree()

    body = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="fr-FR").json()

    assert body[0]["name"] == "Fashion"


def test_arabic_falls_back_to_english_when_translation_is_missing(api_client, tree_url):
    Category.objects.create(name="Shoes", name_en="Shoes")

    body = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar").json()

    assert body[0]["name"] == "Shoes"


def test_category_without_name_en_still_shows_its_legacy_name(api_client, tree_url):
    category = Category.objects.create(name="Legacy")
    Category.objects.filter(pk=category.pk).update(name_en="")

    english = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="en").json()
    arabic = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar").json()

    assert english[0]["name"] == "Legacy"
    assert arabic[0]["name"] == "Legacy"


def test_languages_do_not_leak_through_the_cache(api_client, tree_url):
    _make_fashion_tree()

    first_ar = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar").json()
    first_en = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="en").json()
    second_ar = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar").json()

    assert first_ar[0]["name"] == ARABIC_FASHION
    assert first_en[0]["name"] == "Fashion"
    assert second_ar == first_ar


def test_editing_a_category_invalidates_both_language_caches(api_client, tree_url):
    fashion = _make_fashion_tree()
    api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar")
    api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="en")

    fashion.name_ar = "\u0645\u0644\u0627\u0628\u0633"
    fashion.save()

    ar = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar").json()
    en = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="en").json()
    assert ar[0]["name"] == "\u0645\u0644\u0627\u0628\u0633"
    assert en[0]["name"] == "Fashion"


def test_response_varies_on_accept_language(api_client, tree_url):
    response = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar")

    assert "Accept-Language" in response["Vary"]


def test_response_shape_is_unchanged(api_client, tree_url):
    _make_fashion_tree()

    node = api_client.get(tree_url).json()[0]

    assert set(node.keys()) == {"id", "name", "slug", "children"}


def test_save_fills_name_en_from_name():
    category = Category.objects.create(name="Kids")

    assert category.name == "Kids"
    assert category.name_en == "Kids"
    assert category.name_ar == ""


def test_save_fills_name_from_name_en():
    category = Category.objects.create(name_en="Bags")

    assert category.name == "Bags"
    assert category.slug == "bags"


def test_localized_name_method():
    category = Category(name="Fashion", name_en="Fashion", name_ar=ARABIC_FASHION)

    assert category.localized_name("ar") == ARABIC_FASHION
    assert category.localized_name("en") == "Fashion"
    assert category.localized_name("fr") == "Fashion"
    assert Category(name="X").localized_name("ar") == "X"


def test_data_migration_copies_name_into_name_en():
    category = Category.objects.create(name="Shoes")
    Category.objects.filter(pk=category.pk).update(name_en="")

    module = importlib.import_module("categories.migrations.0003_copy_name_to_name_en")
    module.copy_name_to_name_en(global_apps, None)

    category.refresh_from_db()
    assert category.name_en == "Shoes"
    assert category.name == "Shoes"


def test_data_migration_does_not_overwrite_an_existing_name_en():
    category = Category.objects.create(name="Shoes", name_en="Footwear")

    module = importlib.import_module("categories.migrations.0003_copy_name_to_name_en")
    module.copy_name_to_name_en(global_apps, None)

    category.refresh_from_db()
    assert category.name_en == "Footwear"
    assert category.name == "Shoes"
