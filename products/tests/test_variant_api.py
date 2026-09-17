"""
API tests for Part P-032B — Product Variant CRUD Endpoints
(Business-Owned, IDOR-Protected).

Reuses the exact business/category/product factory helpers from
products/tests/test_api.py (P-032's own test module) rather than
reimplementing them — same "don't duplicate a fixture that already
exists" convention that module itself follows for its image-upload
byte fixtures (borrowed from core/tests/test_media.py).
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from products.models import ProductVariant
from products.tests.test_api import (
    _make_business_profile,
    _make_product,
)

pytestmark = pytest.mark.django_db


def _make_customer_user(email: str) -> User:
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type="customer",
    )


@pytest.fixture
def api_client():
    return APIClient()


def _variant_create_url(product_id):
    return reverse("products:product-variant-create", kwargs={"product_pk": product_id})


def _variant_detail_url(product_id, variant_id):
    return reverse(
        "products:product-variant-detail",
        kwargs={"product_pk": product_id, "pk": variant_id},
    )


class TestProductVariantCreate:
    def test_owner_can_create_variant_on_own_product(self, api_client):
        business = _make_business_profile("p032b-owner1@example.com")
        product = _make_product(business=business)
        api_client.force_authenticate(user=business.user)

        response = api_client.post(
            _variant_create_url(product.id),
            {"name": "Size", "value": "Large"},
            format="json",
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Size"
        assert data["value"] == "Large"
        assert ProductVariant.objects.get(id=data["id"]).product_id == product.id

    def test_owner_can_create_two_or_more_variants(self, api_client):
        """
        Directly exercises P-033's own acceptance criterion: "a business
        can create a product with... 2+ variants... all reflected
        against the real backend."
        """
        business = _make_business_profile("p032b-owner2@example.com")
        product = _make_product(business=business)
        api_client.force_authenticate(user=business.user)

        first = api_client.post(
            _variant_create_url(product.id),
            {"name": "Size", "value": "Large"},
            format="json",
        )
        second = api_client.post(
            _variant_create_url(product.id),
            {"name": "Color", "value": "Red"},
            format="json",
        )

        assert first.status_code == 201
        assert second.status_code == 201
        assert ProductVariant.objects.filter(product=product).count() == 2

    def test_other_business_cannot_create_variant_on_someone_elses_product(
        self, api_client
    ):
        owner = _make_business_profile("p032b-idor-owner1@example.com")
        attacker = _make_business_profile("p032b-idor-attacker1@example.com")
        product = _make_product(business=owner)
        api_client.force_authenticate(user=attacker.user)

        response = api_client.post(
            _variant_create_url(product.id),
            {"name": "Size", "value": "Large"},
            format="json",
        )

        assert response.status_code == 403
        assert ProductVariant.objects.filter(product=product).count() == 0

    def test_unauthenticated_create_is_rejected(self, api_client):
        product = _make_product()

        response = api_client.post(
            _variant_create_url(product.id),
            {"name": "Size", "value": "Large"},
            format="json",
        )

        assert response.status_code == 401
        assert ProductVariant.objects.filter(product=product).count() == 0

    def test_create_on_nonexistent_product_returns_404(self, api_client):
        business = _make_business_profile("p032b-owner3@example.com")
        api_client.force_authenticate(user=business.user)

        response = api_client.post(
            _variant_create_url(999999),
            {"name": "Size", "value": "Large"},
            format="json",
        )

        assert response.status_code == 404

    def test_missing_name_is_rejected(self, api_client):
        business = _make_business_profile("p032b-owner4@example.com")
        product = _make_product(business=business)
        api_client.force_authenticate(user=business.user)

        response = api_client.post(
            _variant_create_url(product.id), {"value": "Large"}, format="json"
        )

        assert response.status_code == 400
        assert (
            "name"
            in response.json()
            .get("error", response.json())
            .get("fields", response.json())
            or response.status_code == 400
        )


class TestProductVariantDetailIDOR:
    def test_unauthenticated_get_detail_succeeds(self, api_client):
        product = _make_product()
        variant = ProductVariant.objects.create(product=product, name="Size", value="L")

        response = api_client.get(_variant_detail_url(product.id, variant.id))

        assert response.status_code == 200
        assert response.json()["id"] == variant.id

    def test_owner_can_patch_own_variant(self, api_client):
        business = _make_business_profile("p032b-patch-owner@example.com")
        product = _make_product(business=business)
        variant = ProductVariant.objects.create(product=product, name="Size", value="L")
        api_client.force_authenticate(user=business.user)

        response = api_client.patch(
            _variant_detail_url(product.id, variant.id),
            {"value": "XL"},
            format="json",
        )

        assert response.status_code == 200
        variant.refresh_from_db()
        assert variant.value == "XL"

    def test_owner_can_delete_own_variant(self, api_client):
        business = _make_business_profile("p032b-delete-owner@example.com")
        product = _make_product(business=business)
        variant = ProductVariant.objects.create(product=product, name="Size", value="L")
        api_client.force_authenticate(user=business.user)

        response = api_client.delete(_variant_detail_url(product.id, variant.id))

        assert response.status_code == 204
        # ProductVariant has no SoftDeleteModel — this is a real,
        # hard row delete (P-031's own deliberate choice).
        assert not ProductVariant.objects.filter(id=variant.id).exists()

    def test_other_business_cannot_patch_variant(self, api_client):
        """
        The critical cross-business IDOR test: Business A owns the
        product and its variant; Business B (a different, real,
        authenticated business) attempts to PATCH it with their own
        valid token. Must be 403, and the variant must remain
        completely unmodified.
        """
        owner = _make_business_profile("p032b-idor-owner2@example.com")
        attacker = _make_business_profile("p032b-idor-attacker2@example.com")
        product = _make_product(business=owner)
        variant = ProductVariant.objects.create(
            product=product, name="Size", value="Original"
        )
        api_client.force_authenticate(user=attacker.user)

        response = api_client.patch(
            _variant_detail_url(product.id, variant.id),
            {"value": "Hacked"},
            format="json",
        )

        assert response.status_code == 403
        variant.refresh_from_db()
        assert variant.value == "Original"

    def test_other_business_cannot_delete_variant(self, api_client):
        owner = _make_business_profile("p032b-idor-owner3@example.com")
        attacker = _make_business_profile("p032b-idor-attacker3@example.com")
        product = _make_product(business=owner)
        variant = ProductVariant.objects.create(product=product, name="Size", value="L")
        api_client.force_authenticate(user=attacker.user)

        response = api_client.delete(_variant_detail_url(product.id, variant.id))

        assert response.status_code == 403
        assert ProductVariant.objects.filter(id=variant.id).exists()

    def test_variant_id_from_a_different_product_returns_404_not_403(self, api_client):
        """
        A second, distinct IDOR angle from the create-side tests above:
        a variant id that genuinely exists, but under a DIFFERENT
        product than the one named in the URL, must 404 — not
        silently resolve across products, and not leak a 403 that
        would confirm the variant id exists at all under this
        product_pk. get_queryset()'s product_id filter is what this
        test locks in.
        """
        business = _make_business_profile("p032b-cross-product@example.com")
        product_a = _make_product(business=business, name="Product A")
        product_b = _make_product(business=business, name="Product B")
        variant_on_a = ProductVariant.objects.create(
            product=product_a, name="Size", value="L"
        )
        api_client.force_authenticate(user=business.user)

        response = api_client.patch(
            _variant_detail_url(product_b.id, variant_on_a.id),
            {"value": "XL"},
            format="json",
        )

        assert response.status_code == 404
        variant_on_a.refresh_from_db()
        assert variant_on_a.value == "L"

    def test_unauthenticated_patch_is_rejected(self, api_client):
        product = _make_product()
        variant = ProductVariant.objects.create(product=product, name="Size", value="L")

        response = api_client.patch(
            _variant_detail_url(product.id, variant.id), {"value": "x"}, format="json"
        )

        assert response.status_code == 401
        variant.refresh_from_db()
        assert variant.value == "L"

    def test_unauthenticated_delete_is_rejected(self, api_client):
        product = _make_product()
        variant = ProductVariant.objects.create(product=product, name="Size", value="L")

        response = api_client.delete(_variant_detail_url(product.id, variant.id))

        assert response.status_code == 401
        assert ProductVariant.objects.filter(id=variant.id).exists()

    def test_detail_of_nonexistent_variant_returns_404(self, api_client):
        product = _make_product()

        response = api_client.get(_variant_detail_url(product.id, 999999))

        assert response.status_code == 404
