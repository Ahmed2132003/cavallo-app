"""
URL routes for the products app (Part P-032).

Included under /api/v1/products/ by config/urls.py.

"public/" is listed before "<int:pk>/" for readability, matching
businesses/urls.py's own convention (P-026) — order doesn't actually
matter here since <int:pk> only matches digit strings and could never
match the literal "public" anyway.
"""

from django.urls import path

from products.views import (
    ProductDetailView,
    ProductListCreateView,
    ProductPublicListView,
)

app_name = "products"

urlpatterns = [
    path("public/", ProductPublicListView.as_view(), name="product-public-list"),
    path("<int:pk>/", ProductDetailView.as_view(), name="product-detail"),
    path("", ProductListCreateView.as_view(), name="product-list-create"),
]