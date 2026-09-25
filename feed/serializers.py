"""
Part P-059 — Home Feed serializers.

Deliberately thin: every feed item is an already-published Post or Reel
(only `published_objects` ever reaches the feed), so item-level fields
are delegated entirely to PostPublicSerializer/ReelPublicSerializer from
content/serializers.py (P-043) rather than duplicated here. This module
only adds the `content_type` discriminator P-061 (Flutter) needs to know
which of the two shapes a given item is.
"""

from rest_framework import serializers

from content.serializers import PostPublicSerializer, ReelPublicSerializer
from feed.cursor import CONTENT_TYPE_POST, CONTENT_TYPE_REEL
from feed.services import FeedEntry

_ITEM_SERIALIZERS = {
    CONTENT_TYPE_POST: PostPublicSerializer,
    CONTENT_TYPE_REEL: ReelPublicSerializer,
}


class FeedItemSerializer(serializers.Serializer):
    """
    Serializes a single feed.services.FeedEntry as
    {"content_type": "post"|"reel", **<PostPublicSerializer or
    ReelPublicSerializer fields>}. Read-only: the Home Feed has no
    writable representation.
    """

    def to_representation(self, instance: FeedEntry):
        serializer_class = _ITEM_SERIALIZERS[instance.content_type]
        data = dict(serializer_class(instance.obj).data)
        data["content_type"] = instance.content_type
        return data
