from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from categories.services import build_category_tree
from core.i18n import get_request_language
from core.cache import cache_get_or_set

CATEGORY_TREE_CACHE_KEY = "categories:tree"
CATEGORY_TREE_CACHE_TTL_SECONDS = 3600  # ~1h, per architecture Section 16

# Part P-112: one cache entry per language, so Arabic and English clients
# never receive each other's names. English keeps the original key.
CATEGORY_TREE_CACHE_KEYS = (
    CATEGORY_TREE_CACHE_KEY,
    f"{CATEGORY_TREE_CACHE_KEY}:ar",
)


def category_tree_cache_key(language):
    if language == "ar":
        return CATEGORY_TREE_CACHE_KEYS[1]
    return CATEGORY_TREE_CACHE_KEYS[0]


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
        language = get_request_language(request)
        tree = cache_get_or_set(
            category_tree_cache_key(language),
            lambda: build_category_tree(language),
            ttl_seconds=CATEGORY_TREE_CACHE_TTL_SECONDS,
        )
        response = Response(tree)
        response["Vary"] = "Accept-Language"
        return response
