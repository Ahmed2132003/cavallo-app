"""
P-096 (step 1) permission / IDOR sweep for the products app.

Covers the 5 write routes (PATCH/DELETE product, POST variant, PATCH/
DELETE variant) against: no credentials (401), a different business
(403), and a customer account (403) - each time asserting the P-012
envelope AND that nothing in the database changed (follow-up query).
Also the cross-product variant id (404, not 403) and product create by
an account with no business profile.
"""

from types import SimpleNamespace

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from businesses.models import BusinessProfile
from categories.models import Category
from core.tests.sweep_helpers import (
    assert_forbidden,
    assert_not_found,
    assert_unauthenticated,
)
from products.models import Product, ProductVariant

pytestmark = pytest.mark.django_db


def _make_user(account_type, email):
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type=account_type,
    )


def _make_business_profile(email):
    return BusinessProfile.objects.create(
        user=_make_user("business", email),
        business_name=f"Business {email}",
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def catalog():
    owner = _make_business_profile("p096-prod-owner@example.com")
    category = Category.objects.create(name="Sweep Fashion")
    product = Product.objects.create(
        business=owner,
        category=category,
        name="Original Name",
        description="A product.",
        price="199.99",
        currency=Product.CURRENCY_EGP,
    )
    variant = ProductVariant.objects.create(
        product=product, name="Size", value="Original"
    )
    return SimpleNamespace(
        owner=owner, category=category, product=product, variant=variant
    )


def _product_url(pk):
    return reverse("products:product-detail", kwargs={"pk": pk})


def _variant_create_url(product_id):
    return reverse("products:product-variant-create", kwargs={"product_pk": product_id})


def _variant_url(product_id, variant_id):
    return reverse(
        "products:product-variant-detail",
        kwargs={"product_pk": product_id, "pk": variant_id},
    )


def _write_requests(c):
    return {
        "patch-product": ("patch", _product_url(c.product.id), {"name": "Hacked"}),
        "delete-product": ("delete", _product_url(c.product.id), None),
        "create-variant": (
            "post",
            _variant_create_url(c.product.id),
            {"name": "Color", "value": "Red"},
        ),
        "patch-variant": (
            "patch",
            _variant_url(c.product.id, c.variant.id),
            {"value": "Hacked"},
        ),
        "delete-variant": ("delete", _variant_url(c.product.id, c.variant.id), None),
    }


WRITE_LABELS = [
    "patch-product",
    "delete-product",
    "create-variant",
    "patch-variant",
    "delete-variant",
]


def _send(client, method, url, payload):
    if method == "delete":
        return client.delete(url)
    return getattr(client, method)(url, payload, format="json")


def _assert_catalog_untouched(c):
    product = Product.all_objects.get(pk=c.product.pk)
    assert product.name == "Original Name"
    assert product.is_deleted is False
    variants = ProductVariant.objects.filter(product_id=c.product.pk)
    assert variants.count() == 1
    assert variants.get().value == "Original"


@pytest.mark.parametrize("label", WRITE_LABELS)
def test_unauthenticated_write_gets_401_and_changes_nothing(api_client, catalog, label):
    method, url, payload = _write_requests(catalog)[label]

    response = _send(api_client, method, url, payload)

    assert_unauthenticated(response)
    _assert_catalog_untouched(catalog)


@pytest.mark.parametrize("actor", ["other_business", "customer"])
@pytest.mark.parametrize("label", WRITE_LABELS)
def test_non_owner_write_gets_403_and_changes_nothing(
    api_client, catalog, label, actor
):
    if actor == "other_business":
        user = _make_business_profile("p096-prod-attacker@example.com").user
    else:
        user = _make_user("customer", "p096-prod-customer@example.com")
    api_client.force_authenticate(user=user)
    method, url, payload = _write_requests(catalog)[label]

    response = _send(api_client, method, url, payload)

    assert_forbidden(response)
    _assert_catalog_untouched(catalog)


def test_unauthenticated_create_and_own_list_get_401_and_create_nothing(api_client):
    category = Category.objects.create(name="Sweep Create")
    payload = {
        "category": category.id,
        "name": "Ghost",
        "description": "x",
        "price": "10.00",
        "currency": Product.CURRENCY_EGP,
    }

    created = api_client.post(
        reverse("products:product-list-create"), payload, format="json"
    )
    listed = api_client.get(reverse("products:product-list-create"))

    assert_unauthenticated(created)
    assert_unauthenticated(listed)
    assert Product.all_objects.count() == 0


def test_customer_without_business_profile_cannot_create_product(api_client):
    category = Category.objects.create(name="Sweep Customer Create")
    api_client.force_authenticate(user=_make_user("customer", "p096-nobiz@example.com"))

    response = api_client.post(
        reverse("products:product-list-create"),
        {
            "category": category.id,
            "name": "Ghost",
            "description": "x",
            "price": "10.00",
            "currency": Product.CURRENCY_EGP,
        },
        format="json",
    )

    assert_forbidden(response)
    assert Product.all_objects.count() == 0


def test_variant_id_of_another_product_is_404_with_envelope_and_untouched(
    api_client, catalog
):
    other = _make_business_profile("p096-prod-other@example.com")
    other_product = Product.objects.create(
        business=other,
        category=catalog.category,
        name="Other Product",
        description="x",
        price="5.00",
        currency=Product.CURRENCY_EGP,
    )
    api_client.force_authenticate(user=other.user)

    patched = api_client.patch(
        _variant_url(other_product.id, catalog.variant.id),
        {"value": "Hacked"},
        format="json",
    )
    deleted = api_client.delete(_variant_url(other_product.id, catalog.variant.id))

    assert_not_found(patched)
    assert_not_found(deleted)
    _assert_catalog_untouched(catalog)


def test_public_reads_stay_open_to_anonymous_users(api_client, catalog):
    detail = api_client.get(_product_url(catalog.product.id))
    variant = api_client.get(_variant_url(catalog.product.id, catalog.variant.id))
    public_list = api_client.get(reverse("products:product-public-list"))

    assert detail.status_code == 200
    assert variant.status_code == 200
    assert public_list.status_code == 200
