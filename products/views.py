"""
Views for Part P-032 — Product CRUD Endpoints (Business-Owned,
IDOR-Protected) + Media Upload, and Part P-032B — Product Variant CRUD
Endpoints (Business-Owned, IDOR-Protected).

Second real application of P-026's "resolve ownership from
request.user, never trust a client-supplied id" IDOR pattern — this
time against a many-owned-resources relationship (one BusinessProfile,
many Products), not P-026's own 1:1 singleton case. Both are valid
implementations of the same underlying principle, per P-026's own
handoff note, and every later many-owned-resources content model
(Posts P-041, Reels P-042, Stories P-046) should copy this file's
pattern rather than a URL-id + permission-class-only approach:

- ProductListCreateView's GET/POST ("my own products") resolves
  ownership exactly like P-026's ``BusinessProfileMeView``: strictly
  from ``request.user.business_profile``, never from a client-supplied
  ``business``/``business_id``. This is the "singleton-owned-resource"
  half of the fork.
- ProductDetailView's PATCH/DELETE necessarily takes a URL-supplied
  product id (there is no single "my product" to resolve to — a
  business owns many), so it cannot be IDOR-safe by construction the
  way ``/me/`` is. Instead, ownership is checked explicitly, inside the
  write path itself (``_check_owner`` below), not merely via a DRF
  permission class alone — this is the "many-owned-resources" half of
  the fork, and the part this file exists to prove.

Part P-032B extends the exact same pattern one level deeper: a
ProductVariant is itself a many-owned-resource of a Product (which is
itself a many-owned-resource of a BusinessProfile), so
ProductVariantCreateView/ProductVariantDetailView below repeat
ProductDetailView's own "resolve by URL id, then explicitly check
ownership inside the write path" shape rather than inventing a new
one.
"""

from rest_framework import generics, permissions
from rest_framework.exceptions import NotFound, PermissionDenied

from core.pagination import StandardCursorPagination

from .models import Product, ProductVariant
from .serializers import ProductSerializer, ProductVariantWriteSerializer


class ProductListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/products/  — list the authenticated business's OWN
                               products (not a public listing — see
                               ProductPublicListView for that).
    POST /api/v1/products/  — create a new product, always attributed to
                               request.user.business_profile.

    Authenticated only. Reuses P-026's exact ownership-resolution
    pattern: both GET and POST resolve the owning BusinessProfile
    strictly from request.user, never from a client-supplied
    business/business_id — see this module's own docstring and
    businesses/views.py's BusinessProfileMeView for the precedent.

    Architecture Section 9 point 7 requires cursor pagination for every
    feed-like/list endpoint; this is the first Product list endpoint,
    so it adopts core.pagination.StandardCursorPagination per that
    rule, ordered by -created_at (matches Product.Meta.ordering).
    """

    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        business = getattr(self.request.user, "business_profile", None)
        if business is None:
            # A Customer-type user, or a Business-type user who hasn't
            # completed onboarding (POST /api/v1/businesses/me/) yet,
            # owns no products. Returning an empty queryset here (rather
            # than raising) keeps GET's behavior symmetric with "you
            # have zero products", not an error — the write path (POST,
            # perform_create below) is where the real "you need a
            # business profile first" rejection happens, matching
            # BusinessProfileMeView's own GET-returns-empty-ish/POST-
            # gates-onboarding split in spirit.
            return Product.objects.none()
        return Product.objects.filter(business=business)

    def perform_create(self, serializer):
        business = getattr(self.request.user, "business_profile", None)
        if business is None:
            raise PermissionDenied(
                "You need a business profile before creating products. "
                "POST /api/v1/businesses/me/ first."
            )
        # `business` is deliberately not a writable ProductSerializer
        # field (see serializers.py's module docstring) — even if the
        # client's request body includes a "business" key (a spoofed
        # id, say), it is not a declared field and is silently dropped
        # during is_valid(), so it never reaches this method's
        # serializer.validated_data at all. This save() call is the one
        # and only place a Product's owner is actually assigned, always
        # from request.user — mirroring P-026's proven
        # create_business_profile(user=request.user, ...) pattern.
        serializer.save(business=business)


class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET            /api/v1/products/{id}/ — public, no auth required.
    PATCH / DELETE /api/v1/products/{id}/ — authenticated; only the
                                             owning business may modify
                                             or delete.

    get_object() necessarily resolves by the URL-supplied primary key
    for every method here (unlike ProductListCreateView's "my own
    products" list) — a product detail view has no "my own X" singleton
    to fall back to. The write-path ownership check below is therefore
    the real IDOR defense for PATCH/DELETE, not merely
    permission_classes: an object-level `if product.business.user !=
    request.user: raise PermissionDenied()` inside perform_update() /
    perform_destroy() themselves, per this part's own explicit
    "defense in depth" requirement (matches Architecture Section 5 rule
    10's emphasis on object-level checks specifically, not just
    endpoint-level ones).
    """

    # The default (soft-delete-aware) manager already excludes
    # soft-deleted rows for every method here, including the public
    # GET — a deleted product is genuinely gone from every read path,
    # not just hidden from its owner.
    queryset = Product.objects.all()
    serializer_class = ProductSerializer

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def _check_owner(self, product):
        """
        The explicit, object-level ownership check this part's spec
        requires — deliberately not left to permission_classes alone.
        product.business is itself resolved by primary key from the
        model's own FK, never re-trusted from anything client-supplied;
        only the *comparison* against request.user happens here.
        """
        if product.business.user_id != self.request.user.id:
            raise PermissionDenied("You do not have permission to modify this product.")

    def perform_update(self, serializer):
        self._check_owner(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._check_owner(instance)
        # Product inherits SoftDeleteModel (P-011/P-031) — this is a
        # soft delete (is_deleted=True, deleted_at=now()), never a real
        # row deletion. See core/models.py.
        instance.delete()


class ProductPublicListView(generics.ListAPIView):
    """
    GET /api/v1/products/public/ — public, read-only list of active
    products, optionally narrowed to one business via ?business_id=.

    This is the endpoint P-029's public business-profile screen is
    meant to eventually consume (per this part's own spec). Public by
    design (AllowAny); only active (is_active=True), non-soft-deleted
    products are ever returned here — a business's hidden/unavailable
    products (the "Hide" / "Mark Out of Stock" actions from the Trader
    app wireframe) must never leak through the one list endpoint that
    has no owner to authenticate against.

    Uses the same StandardCursorPagination as ProductListCreateView,
    per Architecture Section 9 point 7 — this is exactly the kind of
    feed-like list endpoint that rule targets.
    """

    serializer_class = ProductSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        queryset = Product.objects.filter(is_active=True)
        business_id = self.request.query_params.get("business_id")
        if business_id is not None:
            queryset = queryset.filter(business_id=business_id)
        return queryset


class _ProductVariantParentMixin:
    """
    Shared helper for Part P-032B's two variant views below: both need
    to resolve the parent Product from the URL's ``product_pk`` and
    raise a genuine 404 (not a 500, not a silent empty result) when it
    does not exist — deliberately a plain NotFound rather than Django's
    get_object_or_404 shortcut, to stay consistent with this file's
    existing convention of raising DRF's own exception classes directly
    (see PermissionDenied usage above) rather than mixing in
    django.shortcuts helpers.
    """

    def _get_product(self):
        product_pk = self.kwargs["product_pk"]
        try:
            return Product.objects.get(pk=product_pk)
        except Product.DoesNotExist:
            raise NotFound("Product not found.")


class ProductVariantCreateView(_ProductVariantParentMixin, generics.CreateAPIView):
    """
    POST /api/v1/products/{product_pk}/variants/ — create a new variant
    on the given product. Authenticated; only the owning business may
    create a variant on its own product.

    ``product`` is resolved exclusively from the URL's ``product_pk``
    (see _ProductVariantParentMixin), never from anything in the
    request body — ProductVariantWriteSerializer has no ``product``
    field at all, so there is nothing for a client to spoof here in the
    first place (stricter than P-032's "declared but read-only"
    approach for ``business``, and unnecessary to relax: a variant
    genuinely has no legitimate reason to ever target a different
    product than the one in its own URL).
    """

    serializer_class = ProductVariantWriteSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        product = self._get_product()
        if product.business.user_id != self.request.user.id:
            raise PermissionDenied(
                "You do not have permission to add a variant to this product."
            )
        serializer.save(product=product)


class ProductVariantDetailView(
    _ProductVariantParentMixin, generics.RetrieveUpdateDestroyAPIView
):
    """
    GET            /api/v1/products/{product_pk}/variants/{pk}/ — public,
                                             no auth required (mirrors
                                             ProductDetailView's own GET —
                                             a product's variants are
                                             already publicly visible,
                                             nested, via that endpoint).
    PATCH / DELETE /api/v1/products/{product_pk}/variants/{pk}/ —
                                             authenticated; only the
                                             owning business may modify
                                             or delete.

    get_queryset() is scoped to the URL's product_pk, not just filtered
    by the variant's own pk — a variant id that exists but belongs to a
    *different* product must 404, not silently resolve across products
    or leak a cross-product PermissionDenied that would confirm the id
    exists elsewhere. This is a second, distinct IDOR angle from
    ProductDetailView's (which only ever deals with one id, not a
    parent/child pair).
    """

    serializer_class = ProductVariantWriteSerializer

    def get_queryset(self):
        return ProductVariant.objects.filter(product_id=self.kwargs["product_pk"])

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def _check_owner(self, variant):
        """
        Same "resolve the object by primary key from the model's own
        FK, then compare against request.user" shape as
        ProductDetailView._check_owner — here through one extra hop
        (variant.product.business.user_id) since the owning business is
        two FKs away from the variant itself.
        """
        if variant.product.business.user_id != self.request.user.id:
            raise PermissionDenied("You do not have permission to modify this variant.")

    def perform_update(self, serializer):
        self._check_owner(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._check_owner(instance)
        # ProductVariant inherits TimestampedModel only, not
        # SoftDeleteModel (P-031's own deliberate choice — see
        # models.py) — this is therefore a real, hard row delete, not a
        # soft one. That is correct here: a variant genuinely has no
        # independent lifecycle worth preserving once removed.
        instance.delete()
