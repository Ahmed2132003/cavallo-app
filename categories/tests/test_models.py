import pytest
from django.db import IntegrityError
from django.db.models import ProtectedError

from categories.models import Category

pytestmark = pytest.mark.django_db


def test_slug_auto_generated_from_name():
    category = Category.objects.create(name="Fashion")
    assert category.slug == "fashion"


def test_slug_left_untouched_if_explicitly_provided():
    category = Category.objects.create(name="Fashion", slug="custom-slug")
    assert category.slug == "custom-slug"


def test_slug_uniqueness_appends_suffix_on_collision():
    first = Category.objects.create(name="Shoes")
    second = Category.objects.create(name="Shoes", parent=None)
    # unique_together is scoped to (parent, name) so two root "Shoes"
    # rows are allowed at the DB level (see the model's own docstring
    # on the NULL-parent edge case) — but slug is globally unique
    # regardless, so the second one must NOT collide with the first.
    assert first.slug == "shoes"
    assert second.slug != first.slug
    assert second.slug.startswith("shoes")


def test_resaving_existing_category_does_not_change_its_own_slug():
    category = Category.objects.create(name="Kids")
    original_slug = category.slug
    category.is_active = False
    category.save()
    category.refresh_from_db()
    assert category.slug == original_slug


def test_unique_together_blocks_duplicate_name_under_same_parent():
    parent = Category.objects.create(name="Fashion")
    Category.objects.create(name="Men", parent=parent)
    with pytest.raises(IntegrityError):
        Category.objects.create(name="Men", parent=parent)


def test_delete_parent_with_children_is_blocked_not_silently_cascaded():
    parent = Category.objects.create(name="Fashion")
    Category.objects.create(name="Men", parent=parent)

    with pytest.raises(ProtectedError):
        parent.delete()

    # Confirms nothing was silently cascaded away.
    assert Category.objects.filter(pk=parent.pk).exists()
    assert Category.objects.filter(parent=parent).count() == 1


def test_delete_leaf_category_succeeds():
    parent = Category.objects.create(name="Fashion")
    leaf = Category.objects.create(name="Men", parent=parent)

    leaf.delete()

    assert not Category.objects.filter(pk=leaf.pk).exists()
    assert Category.objects.filter(pk=parent.pk).exists()


def test_three_level_tree_parent_child_relationships():
    fashion = Category.objects.create(name="Fashion")
    men = Category.objects.create(name="Men", parent=fashion)
    shoes = Category.objects.create(name="Shoes", parent=men)

    assert shoes.parent_id == men.pk
    assert men.parent_id == fashion.pk
    assert fashion.parent_id is None
    assert list(fashion.children.all()) == [men]
    assert list(men.children.all()) == [shoes]


def test_str_returns_name():
    category = Category.objects.create(name="Electronics")
    assert str(category) == "Electronics"
