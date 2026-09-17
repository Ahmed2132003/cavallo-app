"""
URL routes for the products app (Part P-032, extended by P-032B).

Included under /api/v1/products/ by config/urls.py.

"public/" is listed before "<int:pk>/" for readability, matching
businesses/urls.py's own convention (P-026) — order doesn't actually
matter here since <int:pk> only matches digit strings and could never
match the literal "public" anyway.

Part P-032B's two variant routes are listed before "<int:pk>/" for the
same readability reason (most-specific-first), even though the same
"doesn't actually matter" note applies: "<int:pk>/" cannot match
"5/variants/" or "5/variants/3/" either, since those have segments
after the leading digits that "<int:pk>/" alone does not account for.
"""

from django.urls import path

from products.views import (
    ProductDetailView,
    ProductListCreateView,
    ProductPublicListView,
    ProductVariantCreateView,
    ProductVariantDetailView,
)

app_name = "products"

urlpatterns = [
    path("public/", ProductPublicListView.as_view(), name="product-public-list"),
    path(
        "<int:product_pk>/variants/",
        ProductVariantCreateView.as_view(),
        name="product-variant-create",
    ),
    path(
        "<int:product_pk>/variants/<int:pk>/",
        ProductVariantDetailView.as_view(),
        name="product-variant-detail",
    ),
    path("<int:pk>/", ProductDetailView.as_view(), name="product-detail"),
    path("", ProductListCreateView.as_view(), name="product-list-create"),
]
