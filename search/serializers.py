"""
Part P-064 STEP 2 — Search result serializers.

Deliberately thin, mirroring feed/serializers.py's own reasoning
(P-059): every result is either an already-visible BusinessProfile or
Product, so field-level representation is delegated entirely to the
existing BusinessProfileSerializer (businesses/serializers.py, P-026)
and ProductSerializer (products/serializers.py, P-032) rather than
duplicated here — per this part's own Files Expected note to "reuse
existing public serializers ... where possible rather than
duplicating field definitions".

This module adds only the `result_type` discriminator this part's own
Scope section calls for ("a unified list with a result_type field is
recommended for simpler Flutter consumption"). Named `result_type`,
NOT `content_type`: feed/serializers.py's FeedItemSerializer already
uses `content_type` for a different pair of values ("post"/"reel"),
and this endpoint's own wire contract distinguishes "business"/
"product" instead — reusing the same key name for two different value
sets would be a silent trap for P-065 (Flutter) if it ever consumes
both endpoints in the same screen.
"""

from rest_framework import serializers

from businesses.serializers import BusinessProfileSerializer
from products.serializers import ProductSerializer
from search.cursor import CONTENT_TYPE_BUSINESS, CONTENT_TYPE_PRODUCT
from search.services import SearchResult

_ITEM_SERIALIZERS = {
    CONTENT_TYPE_BUSINESS: BusinessProfileSerializer,
    CONTENT_TYPE_PRODUCT: ProductSerializer,
}


class SearchResultSerializer(serializers.Serializer):
    """
    Serializes a single search.services.SearchResult as
    {"result_type": "business"|"product", **<BusinessProfileSerializer
    or ProductSerializer fields>}. Read-only: search has no writable
    representation.
    """

    def to_representation(self, instance: SearchResult):
        serializer_class = _ITEM_SERIALIZERS[instance.content_type]
        data = dict(serializer_class(instance.obj).data)
        data["result_type"] = instance.content_type
        return data