import pytest
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError

from accounts.models import User
from businesses.models import BusinessProfile
from categories.models import Category
from products.models import Product, ProductVariant

pytestmark = pytest.mark.django_db


def _make_business(email: str) -> BusinessProfile:
    user = User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type="business",
    )
    return BusinessProfile.objects.create(
        user=user,
        business_name="Acme Trading",
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )


def _make_category(name: str = "Fashion") -> Category:
    return Category.objects.create(name=name)


def _make_product(business=None, category=None, **overrides) -> Product:
    defaults = {
        "business": business or _make_business("p031-owner@example.com"),
        "category": category or _make_category(),
        "name": "Classic Shirt",
        "description": "A shirt.",
        "price": "199.99",
        "currency": Product.CURRENCY_EGP,
    }
    defaults.update(overrides)
    return Product.objects.create(**defaults)


class TestProductVariantRelationship:
    def test_product_with_two_variants_creates_and_queries_correctly(self):
        product = _make_product()
        ProductVariant.objects.create(product=product, name="Size", value="M")
        ProductVariant.objects.create(product=product, name="Size", value="L")

        variants = product.variants.order_by("id")

        assert variants.count() == 2
        assert [v.value for v in variants] == ["M", "L"]
        assert all(v.product_id == product.id for v in variants)

    def test_variant_str_reads_name_and_value(self):
        product = _make_product()
        variant = ProductVariant.objects.create(
            product=product, name="Color", value="Black"
        )
        assert str(variant) == "Color: Black"


class TestProductCurrencyChoices:
    def test_full_clean_rejects_currency_outside_choice_set(self):
        product = _make_product()
        product.currency = "USD"  # not in Product.CURRENCY_CHOICES
        with pytest.raises(ValidationError):
            product.full_clean()

    def test_full_clean_accepts_every_documented_currency(self):
        product = _make_product()
        for code, _label in Product.CURRENCY_CHOICES:
            product.currency = code
            product.full_clean()  # must not raise for any documented code

    def test_direct_save_does_not_enforce_choices_known_gap(self):
        """Documents a known, flagged limitation (see models.py's
        Product.currency comment): Django's `choices` is a form/
        full_clean()-level validation only, never a database-level
        constraint. A direct .save() bypassing full_clean() currently
        lets an invalid currency through. This is not a bug in this
        part - it is the exact gap P-032's ProductSerializer MUST
        close on the real write path. This test exists so that gap
        cannot silently regress or be forgotten."""
        product = _make_product()
        product.currency = "USD"
        product.save()  # does NOT call full_clean() - no error raised
        product.refresh_from_db()
        assert product.currency == "USD"


class TestProductForeignKeyIntegrity:
    def test_business_soft_delete_does_not_touch_products(self):
        business = _make_business("p031-softdel@example.com")
        product = _make_product(business=business)

        business.delete()  # SoftDeleteModel.delete() - never hits PROTECT

        product.refresh_from_db()
        assert product.business_id == business.id
        business.refresh_from_db()
        assert business.is_deleted is True

    def test_business_hard_delete_blocked_by_protect_when_products_exist(self):
        business = _make_business("p031-harddel@example.com")
        _make_product(business=business)

        with pytest.raises(ProtectedError):
            business.hard_delete()

    def test_business_hard_delete_succeeds_once_products_are_gone(self):
        business = _make_business("p031-harddel-clean@example.com")
        product = _make_product(business=business)
        product.delete()  # Product's own soft delete...
        product.hard_delete()  # ...but PROTECT checks real DB rows, so
        # the product row itself must be truly gone, not just
        # soft-deleted, before the business can be hard-deleted.

        business.hard_delete()  # must not raise now

        assert BusinessProfile.all_objects.filter(pk=business.pk).exists() is False

    # Bonus coverage, same architectural decision as the business FK
    # above (see models.py's Product.category comment) - not
    # explicitly named in this part's Testing section, but cheap to
    # add and guards the identical PROTECT choice on the category FK.
    def test_category_hard_delete_blocked_by_protect_when_products_exist(self):
        category = _make_category("Electronics")
        _make_product(category=category)

        with pytest.raises(ProtectedError):
            category.delete()  # Category has no soft delete (P-025) -
            # its own .delete() IS the real, hard delete.
