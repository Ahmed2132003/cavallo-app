from rest_framework import generics, permissions
from rest_framework.exceptions import PermissionDenied

from core.pagination import StandardCursorPagination
from stories.models import Story
from stories.serializers import StorySerializer


class StoryListCreateView(generics.ListCreateAPIView):
    """
    Part P-046 / P-047. GET: the authenticated business's own stories only
    (same ownership pattern as PostListCreateView/ReelListCreateView —
    resolved from request.user.business_profile), across ALL statuses
    (pending_review/published/rejected) since this is the owner's own
    management view, not a public feed. Ordered most-recent-first via
    StandardCursorPagination's built-in `-created_at` ordering (no extra
    .order_by() needed here). POST: always attributes the new Story via
    serializer.save(business=...) in perform_create(). A "business" key
    in the request body is never honored (already dropped by the
    serializer's read_only_fields — this is the second layer of the
    same guarantee).

    A public, expiry-aware "stories visible to customers right now"
    listing is explicitly OUT of this part's scope (see P-047's spec's
    "Out of Scope" section) — that belongs to P-048 once the
    expiry-sweep mechanism exists. This view's GET is the owner's own
    list only, never a public feed.

    Renamed from P-046's StoryCreateView to StoryListCreateView in
    P-047, matching the PostListCreateView/ReelListCreateView naming
    convention and this part's own spec — no behavior change.
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