from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from categories.services import build_category_tree
from core.cache import cache_get_or_set

CATEGORY_TREE_CACHE_KEY = "categories:tree"
CATEGORY_TREE_CACHE_TTL_SECONDS = 3600  # ~1h, per architecture Section 16


class CategoryTreeView(APIView):
    """GET /api/v1/categories/tree/ — public, unauthenticated, read-only.

    No write endpoint exists here on purpose (Admin-only management via
    Django Admin, per this part's explicit scope) — do not add a POST/
    PUT/PATCH/DELETE here in a future part without a dedicated,
    explicitly-scoped part for it.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        tree = cache_get_or_set(
            CATEGORY_TREE_CACHE_KEY,
            build_category_tree,
            ttl_seconds=CATEGORY_TREE_CACHE_TTL_SECONDS,
        )
        return Response(tree)
