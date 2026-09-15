from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient

from categories.models import Category
from categories.services import build_category_tree
from categories.views import CATEGORY_TREE_CACHE_KEY

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_category_tree_cache():
    # Real Redis cache (per P-014's own convention: cache correctness
    # is tested against the real Compose Redis service, not mocked) —
    # must be cleared before/after each test so tests never leak the
    # cached tree into one another via a shared key.
    cache.delete(CATEGORY_TREE_CACHE_KEY)
    yield
    cache.delete(CATEGORY_TREE_CACHE_KEY)


@pytest.fixture
def api_client():
    return APIClient()


def test_tree_endpoint_returns_nested_structure(api_client):
    fashion = Category.objects.create(name="Fashion")
    Category.objects.create(name="Men", parent=fashion)
    Category.objects.create(name="Women", parent=fashion)

    url = reverse("categories:category-tree")
    response = api_client.get(url)

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Fashion"
    assert {c["name"] for c in data[0]["children"]} == {"Men", "Women"}


def test_tree_endpoint_is_public_unauthenticated(api_client):
    Category.objects.create(name="Fashion")
    url = reverse("categories:category-tree")

    # No credentials/auth header set at all.
    response = api_client.get(url)

    assert response.status_code == 200


def test_tree_endpoint_hits_cache_on_second_call(api_client):
    Category.objects.create(name="Fashion")
    url = reverse("categories:category-tree")

    with patch(
        "categories.views.build_category_tree", wraps=build_category_tree
    ) as spy:
        first = api_client.get(url)
        second = api_client.get(url)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    # The whole point of cache_get_or_set: compute_fn only runs once,
    # the second call is served from Redis.
    assert spy.call_count == 1


def test_admin_edit_invalidates_cache_immediately_not_after_ttl(api_client):
    """Simulates an Admin edit via the ORM directly (same effect as a
    Django Admin save) and confirms the very next call to the tree
    endpoint reflects it immediately — the cache must NOT still be
    serving the pre-edit ~1h-old snapshot.
    """
    url = reverse("categories:category-tree")

    # Warm the cache with an empty tree.
    first = api_client.get(url)
    assert first.json() == []

    # Simulate an Admin creating a category in Django Admin.
    Category.objects.create(name="Fashion")

    second = api_client.get(url)
    assert second.json() != []
    assert second.json()[0]["name"] == "Fashion"


def test_admin_delete_invalidates_cache_immediately(api_client):
    fashion = Category.objects.create(name="Fashion")
    url = reverse("categories:category-tree")

    first = api_client.get(url)
    assert first.json()[0]["name"] == "Fashion"

    fashion.delete()

    second = api_client.get(url)
    assert second.json() == []
