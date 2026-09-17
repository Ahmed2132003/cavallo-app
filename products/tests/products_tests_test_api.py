"""
API tests for Part P-032 — Product CRUD Endpoints (Business-Owned,
IDOR-Protected) + Media Upload.

Uses force_authenticate() (same convention as
businesses/tests/test_api.py and accounts/tests/test_permissions.py)
since these tests are about ownership/IDOR/validation logic, not the
JWT login flow itself.

Reuses the exact spoofed-extension / valid-PNG byte fixtures from
core/tests/test_media.py (P-013's own test module) rather than
reimplementing a second, possibly-weaker version of them, per this
part's own explicit acceptance criterion ("confirm this isn't a
separately (and possibly more weakly) reimplemented check").
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from businesses.models import BusinessProfile
from categories.models import Category
from core.tests.test_media import _DISGUISED_EXE_BYTES, _VALID_PNG_BYTES
from products.models import Product, ProductVariant

pytestmark = pytest.mark.django_db


def _make_business_user(email: str) -> User:
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type="business",
    )


def _make_customer_user(email: str) -> User:
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type="customer",
    )


def _make_business_profile(email: str) -> BusinessProfile:
    user = _make_business_user(email)
    return BusinessProfile.objects.create(
        user=user,
        business_name=f"Business {email}",
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )


def _make_category(name: str = "Fashion") -> Category:
    return Category.objects.create(name=name)


def _make_product(business=None, category=None, **overrides) -> Product:
    defaults = {
        "business": business or _make_business_profile("p032-owner@example.com"),
        "category": category or _make_category(),
        "name": "Classic Shirt",
        "description": "A shirt.",
        "price": "199.99",
        "currency": Product.CURRENCY_EGP,
    }
    defaults.update(overrides)
    return Product.objects.create(**defaults)


@pytest.fixture
def api_client():
    return APIClient()


PRODUCT_LIST_CREATE_URL = reverse("products:product-list-create")
PRODUCT_PUBLIC_LIST_URL = reverse("products:product-public-list")


def _product_detail_url(pk):
    return reverse("products:product-detail", kwargs={"pk": pk})


def _valid_product_payload(category_id):
    return {
        "category": category_id,
        "name": "Handmade Lamp",
        "description": "A lamp.",
        "price": "349.50",
        "currency": Product.CURRENCY_EGP,
    }


class TestProductCreate:
    def test_business_user_can_create_own_product(self, api_client):
        business = _make_business_profile("p032-biz1@example.com")
        category = _make_category()
        api_client.force_authenticate(user=business.user)

        response = api_client.post(
            PRODUCT_LIST_CREATE_URL,
            _valid_product_payload(category.id),
            format="json",
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Handmade Lamp"
        assert data["business"] == business.id
        assert Product.objects.get(id=data["id"]).business_id == business.id

    def test_business_field_in_body_is_ignored_not_honored(self, api_client):
        """
        Critical IDOR test (create side): even if the client's request
        body includes a "business" field pointing at a different,
        real business, the created product must still be attributed to
        the authenticated user's own business — never the spoofed
        value.
        """
        own_business = _make_business_profile("p032-spoofer@example.com")
        other_business = _make_business_profile("p032-victim1@example.com")
        category = _make_category()
        api_client.force_authenticate(user=own_business.user)

        payload = _valid_product_payload(category.id)
        payload["business"] = other_business.id

        response = api_client.post(PRODUCT_LIST_CREATE_URL, payload, format="json")

        assert response.status_code == 201
        assert response.json()["business"] == own_business.id
        product = Product.objects.get(id=response.json()["id"])
        assert product.business_id == own_business.id
        assert product.business_id != other_business.id

    def test_customer_without_business_profile_cannot_create_product(self, api_client):
        customer = _make_customer_user("p032-cust1@example.com")
        category = _make_category()
        api_client.force_authenticate(user=customer)

        response = api_client.post(
            PRODUCT_LIST_CREATE_URL,
            _valid_product_payload(category.id),
            format="json",
        )

        assert response.status_code == 403

    def test_unauthenticated_create_is_rejected(self, api_client):
        category = _make_category()

        response = api_client.post(
            PRODUCT_LIST_CREATE_URL,
            _valid_product_payload(category.id),
            format="json",
        )

        assert response.status_code == 401

    def test_invalid_currency_is_rejected(self, api_client):
        """
        Closes P-031's own explicitly flagged gap: Product.currency's
        `choices` is not enforced at the raw .save()/DB level, so this
        serializer (the real write path) must be the layer that
        rejects an invalid currency.
        """
        business = _make_business_profile("p032-biz-currency@example.com")
        category = _make_category()
        api_client.force_authenticate(user=business.user)

        payload = _valid_product_payload(category.id)
        payload["currency"] = "USD"  # not in Product.CURRENCY_CHOICES

        response = api_client.post(PRODUCT_LIST_CREATE_URL, payload, format="json")

        assert response.status_code == 400
        assert "currency" in response.json()["error"]["fields"]
        assert not Product.objects.filter(name="Handmade Lamp").exists()


class TestProductOwnList:
    def test_list_returns_only_own_products(self, api_client):
        business_a = _make_business_profile("p032-lista@example.com")
        business_b = _make_business_profile("p032-listb@example.com")
        category = _make_category()
        _make_product(business=business_a, category=category, name="A's Product")
        _make_product(business=business_b, category=category, name="B's Product")

        api_client.force_authenticate(user=business_a.user)
        response = api_client.get(PRODUCT_LIST_CREATE_URL)

        assert response.status_code == 200
        names = [item["name"] for item in response.json()["results"]]
        assert names == ["A's Product"]

    def test_business_id_in_query_params_is_ignored_for_own_list(self, api_client):
        """
        The "my own products" list is filtered exclusively from
        request.user.business_profile — it must never accept a
        business_id query param the way the public list does (that
        would defeat the point of this being the "own products" view).
        """
        business_a = _make_business_profile("p032-lista2@example.com")
        business_b = _make_business_profile("p032-listb2@example.com")
        category = _make_category()
        _make_product(business=business_a, category=category, name="A's Product")
        _make_product(business=business_b, category=category, name="B's Product")

        api_client.force_authenticate(user=business_a.user)
        response = api_client.get(
            PRODUCT_LIST_CREATE_URL, {"business_id": business_b.id}
        )

        names = [item["name"] for item in response.json()["results"]]
        assert names == ["A's Product"]

    def test_customer_without_business_profile_sees_empty_list(self, api_client):
        customer = _make_customer_user("p032-cust2@example.com")
        api_client.force_authenticate(user=customer)

        response = api_client.get(PRODUCT_LIST_CREATE_URL)

        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_unauthenticated_own_list_is_rejected(self, api_client):
        response = api_client.get(PRODUCT_LIST_CREATE_URL)
        assert response.status_code == 401


class TestProductDetailIDOR:
    def test_unauthenticated_get_detail_succeeds(self, api_client):
        product = _make_product()

        response = api_client.get(_product_detail_url(product.id))

        assert response.status_code == 200
        assert response.json()["id"] == product.id

    def test_owner_can_patch_own_product(self, api_client):
        business = _make_business_profile("p032-owner-patch@example.com")
        product = _make_product(business=business)
        api_client.force_authenticate(user=business.user)

        response = api_client.patch(
            _product_detail_url(product.id),
            {"name": "Updated Name"},
            format="json",
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Updated Name"
        product.refresh_from_db()
        assert product.name == "Updated Name"

    def test_owner_can_delete_own_product(self, api_client):
        business = _make_business_profile("p032-owner-delete@example.com")
        product = _make_product(business=business)
        api_client.force_authenticate(user=business.user)

        response = api_client.delete(_product_detail_url(product.id))

        assert response.status_code == 204
        # SoftDeleteModel: the row still exists, just excluded from the
        # default (non-deleted) manager and now invisible to a public
        # GET.
        assert not Product.objects.filter(id=product.id).exists()
        assert Product.all_objects.get(id=product.id).is_deleted is True
        get_response = api_client.get(_product_detail_url(product.id))
        assert get_response.status_code == 404

    def test_other_business_cannot_patch_product(self, api_client):
        """
        The critical cross-business IDOR test: Business A creates a
        product; Business B (a different, real, authenticated business)
        attempts to PATCH it using their own valid token. Must be 403,
        and the product must remain completely unmodified — verified by
        re-fetching it.
        """
        owner = _make_business_profile("p032-idor-owner1@example.com")
        attacker = _make_business_profile("p032-idor-attacker1@example.com")
        product = _make_product(business=owner, name="Original Name")
        api_client.force_authenticate(user=attacker.user)

        response = api_client.patch(
            _product_detail_url(product.id),
            {"name": "Hacked Name"},
            format="json",
        )

        assert response.status_code == 403
        product.refresh_from_db()
        assert product.name == "Original Name"

    def test_other_business_cannot_delete_product(self, api_client):
        owner = _make_business_profile("p032-idor-owner2@example.com")
        attacker = _make_business_profile("p032-idor-attacker2@example.com")
        product = _make_product(business=owner)
        api_client.force_authenticate(user=attacker.user)

        response = api_client.delete(_product_detail_url(product.id))

        assert response.status_code == 403
        assert Product.objects.filter(id=product.id).exists()
        product.refresh_from_db()
        assert product.is_deleted is False

    def test_unauthenticated_patch_is_rejected(self, api_client):
        product = _make_product()

        response = api_client.patch(
            _product_detail_url(product.id), {"name": "x"}, format="json"
        )

        assert response.status_code == 401
        product.refresh_from_db()
        assert product.name != "x"

    def test_unauthenticated_delete_is_rejected(self, api_client):
        product = _make_product()

        response = api_client.delete(_product_detail_url(product.id))

        assert response.status_code == 401
        assert Product.objects.filter(id=product.id).exists()

    def test_get_detail_includes_variants(self, api_client):
        product = _make_product()
        ProductVariant.objects.create(product=product, name="Size", value="L")

        response = api_client.get(_product_detail_url(product.id))

        assert response.status_code == 200
        assert response.json()["variants"] == [
            {"id": ProductVariant.objects.get().id, "name": "Size", "value": "L"}
        ]


class TestProductPublicList:
    def test_public_list_requires_no_auth(self, api_client):
        _make_product(name="Public Product")

        response = api_client.get(PRODUCT_PUBLIC_LIST_URL)

        assert response.status_code == 200
        names = [item["name"] for item in response.json()["results"]]
        assert names == ["Public Product"]

    def test_public_list_filters_by_business_id(self, api_client):
        business_a = _make_business_profile("p032-pub-a@example.com")
        business_b = _make_business_profile("p032-pub-b@example.com")
        category = _make_category()
        _make_product(business=business_a, category=category, name="A's Product")
        _make_product(business=business_b, category=category, name="B's Product")

        response = api_client.get(
            PRODUCT_PUBLIC_LIST_URL, {"business_id": business_a.id}
        )

        names = [item["name"] for item in response.json()["results"]]
        assert names == ["A's Product"]

    def test_public_list_excludes_inactive_products(self, api_client):
        business = _make_business_profile("p032-pub-inactive@example.com")
        category = _make_category()
        _make_product(
            business=business,
            category=category,
            name="Active Product",
            is_active=True,
        )
        _make_product(
            business=business,
            category=category,
            name="Hidden Product",
            is_active=False,
        )

        response = api_client.get(PRODUCT_PUBLIC_LIST_URL)

        names = [item["name"] for item in response.json()["results"]]
        assert names == ["Active Product"]

    def test_public_list_excludes_soft_deleted_products(self, api_client):
        business = _make_business_profile("p032-pub-deleted@example.com")
        product = _make_product(business=business, name="Will Be Deleted")
        api_client.force_authenticate(user=business.user)
        api_client.delete(_product_detail_url(product.id))

        response = api_client.get(PRODUCT_PUBLIC_LIST_URL)

        names = [item["name"] for item in response.json()["results"]]
        assert "Will Be Deleted" not in names


class TestProductImageUpload:
    """
    Reuses core/tests/test_media.py's own byte fixtures (P-013) rather
    than reimplementing a second version, per this part's own
    acceptance criterion.
    """

    def test_valid_image_upload_is_accepted(self, api_client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        business = _make_business_profile("p032-img-valid@example.com")
        category = _make_category()
        api_client.force_authenticate(user=business.user)

        payload = _valid_product_payload(category.id)
        payload["image"] = SimpleUploadedFile(
            "photo.png", _VALID_PNG_BYTES, content_type="image/png"
        )

        response = api_client.post(PRODUCT_LIST_CREATE_URL, payload, format="multipart")

        assert response.status_code == 201
        assert response.json()["image"] is not None

    def test_spoofed_extension_image_is_rejected(self, api_client):
        """
        The exact "disguised executable" case Architecture Section 28
        requires: an executable renamed to .jpg (correct extension,
        correct claimed Content-Type, wrong actual content) must be
        rejected via core.media.validate_upload()'s real content
        sniffing — not a separately (and possibly more weakly)
        reimplemented check.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        business = _make_business_profile("p032-img-spoof@example.com")
        category = _make_category()
        api_client.force_authenticate(user=business.user)

        payload = _valid_product_payload(category.id)
        payload["image"] = SimpleUploadedFile(
            "totally_a_photo.jpg",
            _DISGUISED_EXE_BYTES,
            content_type="image/jpeg",
        )

        response = api_client.post(PRODUCT_LIST_CREATE_URL, payload, format="multipart")

        assert response.status_code == 400
        assert "image" in response.json()["error"]["fields"]
        assert not Product.objects.filter(name="Handmade Lamp").exists()

    def test_product_without_image_is_still_valid(self, api_client):
        business = _make_business_profile("p032-img-none@example.com")
        category = _make_category()
        api_client.force_authenticate(user=business.user)

        response = api_client.post(
            PRODUCT_LIST_CREATE_URL,
            _valid_product_payload(category.id),
            format="json",
        )

        assert response.status_code == 201
        assert response.json()["image"] is None