import pytest

from categories.models import Category
from categories.services import build_category_tree

pytestmark = pytest.mark.django_db


def test_two_level_tree_serializes_correctly():
    fashion = Category.objects.create(name="Fashion")
    Category.objects.create(name="Men", parent=fashion)
    Category.objects.create(name="Women", parent=fashion)

    tree = build_category_tree()

    assert len(tree) == 1
    root = tree[0]
    assert root["name"] == "Fashion"
    assert root["slug"] == "fashion"
    assert {child["name"] for child in root["children"]} == {"Men", "Women"}
    for child in root["children"]:
        assert child["children"] == []


def test_three_level_tree_serializes_correctly():
    fashion = Category.objects.create(name="Fashion")
    men = Category.objects.create(name="Men", parent=fashion)
    Category.objects.create(name="Shoes", parent=men)

    tree = build_category_tree()

    root = tree[0]
    men_node = root["children"][0]
    assert men_node["name"] == "Men"
    assert len(men_node["children"]) == 1
    assert men_node["children"][0]["name"] == "Shoes"


def test_inactive_category_excluded_from_tree():
    fashion = Category.objects.create(name="Fashion")
    Category.objects.create(name="Discontinued", parent=fashion, is_active=False)
    Category.objects.create(name="Men", parent=fashion)

    tree = build_category_tree()

    root = tree[0]
    names = {child["name"] for child in root["children"]}
    assert names == {"Men"}


def test_active_child_of_inactive_parent_surfaces_as_root():
    # Documented behavior (see services.build_category_tree's own
    # docstring): filtering only on is_active per-row means a category
    # whose *parent* is inactive doesn't vanish — it surfaces at the
    # top level instead of being silently dropped from the tree.
    fashion = Category.objects.create(name="Fashion", is_active=False)
    men = Category.objects.create(name="Men", parent=fashion)

    tree = build_category_tree()

    assert [node["name"] for node in tree] == ["Men"]
    assert tree[0]["id"] == men.pk


def test_empty_tree_when_no_categories_exist():
    assert build_category_tree() == []
