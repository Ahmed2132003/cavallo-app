from rest_framework import generics, permissions
from rest_framework.exceptions import PermissionDenied

from core.pagination import StandardCursorPagination
from stories.models import Story
from stories.serializers import StorySerializer


class StoryCreateView(generics.ListCreateAPIView):
    """
    Part P-046. GET: the authenticated business's own stories only
    (same ownership pattern as PostListCreateView/ReelListCreateView —
    resolved from request.user.business_profile). POST: always
    attributes the new Story via serializer.save(business=...) in
    perform_create(). A "business" key in the request body is never
    honored (already dropped by the serializer's read_only_fields —
    this is the second layer of the same guarantee).

    A public, expiry-aware "stories visible to customers right now"
    listing is explicitly OUT of this part's scope (see this part's
    spec's "Out of Scope" section) — that belongs to a future part once
    the expiry-sweep mechanism exists. This view's GET is the owner's
    own list only, never a public feed.
    """

    serializer_class = StorySerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        business = getattr(self.request.user, "business_profile", None)
        if business is None:
            return Story.objects.none()
        return Story.objects.filter(business=business)

    def perform_create(self, serializer):
        business = getattr(self.request.user, "business_profile", None)
        if business is None:
            raise PermissionDenied(
                "Only an authenticated business account can create stories."
            )
        serializer.save(business=business)