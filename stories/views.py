from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardCursorPagination
from stories.models import Story, StoryView
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

    Public, expiry-aware "stories visible to customers right now"
    listing lives separately, in StoryPublicListView below (Part
    P-048) — this view's GET stays the owner's own list only, never a
    public feed.

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


class StoryPublicListView(generics.ListAPIView):
    """
    Part P-048. Public, unauthenticated, expiry-aware "stories visible
    to customers right now" endpoint — GET /api/v1/stories/public/.

    THIS is architecture Section 9's visibility gate in concrete form:
    the queryset filters directly on
    Story.objects.filter(status=Story.Status.PUBLISHED,
    expires_at__gt=timezone.now()), evaluated fresh on every single
    request via the ORM. This condition is genuinely independent of
    stories/tasks.py's expire_stale_stories() sweep job — a Story
    exactly 1 second past its expires_at is invisible here immediately,
    whether or not the sweep job has ever run. `archived_at` (set only
    by that job) is NEVER referenced in this queryset — see
    stories/models.py's archived_at field docstring for why checking it
    here would be an architecture violation, not just redundant.

    Optional ?business_id=<id> query param narrows to one business's
    stories (e.g. for a Business Profile page's story ring, per the
    presentation deck). No ownership/ownership-filtering here — this
    view is intentionally AllowAny, matching the presentation deck's
    customer-facing Home Feed / Business Profile "Stories" sections,
    which any customer (authenticated or not) can view.
    """

    serializer_class = StorySerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        queryset = Story.objects.filter(
            status=Story.Status.PUBLISHED,
            expires_at__gt=timezone.now(),
        )
        business_id = self.request.query_params.get("business_id")
        if business_id is not None:
            queryset = queryset.filter(business_id=business_id)
        return queryset


def _get_story_or_404(pk):
    """
    Part P-049. Same DRF-NotFound-explicitly convention as
    content/views.py's _get_post_or_404 — a genuine 404 with the
    NOT_FOUND error code, not django.shortcuts.get_object_or_404's
    generic Http404.
    """
    story = Story.objects.filter(pk=pk).first()
    if story is None:
        raise NotFound("Story not found.")
    return story


class StoryViewRecordView(APIView):
    """
    Part P-049. POST /api/v1/stories/{id}/view/ — authenticated.

    Records that request.user viewed this story. Idempotent by
    construction: StoryView.objects.get_or_create(story=..., viewer=...)
    relies on the model's own unique_together = ('story', 'viewer')
    constraint, so a repeat call from the same user never creates a
    second row and never raises — no try/except IntegrityError needed
    here. Returns 200 whether the row was newly created or already
    existed; the client is deliberately never told which, since it
    doesn't need to know (per this part's own spec).

    No ownership/IDOR check here on purpose — any authenticated user
    (the story's own owner included) may record a view. The IDOR-
    protected side of this feature is the *count* (StoryViewCountView
    below), not the record action itself.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        story = _get_story_or_404(pk)
        StoryView.objects.get_or_create(story=story, viewer=request.user)
        return Response(status=status.HTTP_200_OK)


class StoryViewCountView(APIView):
    """
    Part P-049. GET /api/v1/stories/{id}/view-count/ — authenticated,
    owner-only.

    Same explicit object-level IDOR-check pattern established since
    P-026/P-032 (see products/views.py's ProductDetailView._check_owner
    and content/views.py's PostDetailView._check_ownership): compares
    story.business.user_id against request.user.id directly, raising
    PermissionDenied on mismatch, rather than relying on
    permission_classes alone.

    story.storyview_set.count() uses the model's default reverse
    accessor (StoryView.story has no explicit related_name — see
    stories/models.py's StoryView docstring) — this is the exact
    literal accessor name this part's own spec calls out.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        story = _get_story_or_404(pk)
        if story.business.user_id != request.user.id:
            raise PermissionDenied(
                "You do not have permission to view this story's view count."
            )
        return Response({"view_count": story.storyview_set.count()})
