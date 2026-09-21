from rest_framework import generics, permissions
from rest_framework.exceptions import NotFound, PermissionDenied

from content.models import Post, Reel
from content.serializers import (
    PostPublicSerializer,
    PostSerializer,
    ReelPublicSerializer,
    ReelSerializer,
)
from content.tasks import transcode_reel
from core.pagination import StandardCursorPagination


def _get_post_or_404(pk):
    """
    DRF NotFound explicitly, not django.shortcuts.get_object_or_404 —
    per the P-038 rule, so the error envelope's code is NOT_FOUND, not
    the generic ERROR fallback.
    """
    post = Post.objects.filter(pk=pk).first()
    if post is None:
        raise NotFound("Post not found.")
    return post


def _get_reel_or_404(pk):
    reel = Reel.objects.filter(pk=pk).first()
    if reel is None:
        raise NotFound("Reel not found.")
    return reel


class PostListCreateView(generics.ListCreateAPIView):
    """
    GET: the authenticated business's own posts only, resolved from
    request.user.business_profile (P-032's exact pattern — the real
    attribute has the underscore).
    POST: always attributes the new Post via serializer.save(business=...)
    in perform_create(). A "business" key in the request body is never
    honored (already dropped by the serializer's read_only_fields, this
    is the second layer of the same guarantee).
    """

    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        business = getattr(self.request.user, "business_profile", None)
        if business is None:
            return Post.objects.none()
        return Post.objects.filter(business=business)

    def perform_create(self, serializer):
        business = getattr(self.request.user, "business_profile", None)
        if business is None:
            raise PermissionDenied(
                "Only an authenticated business account can create posts."
            )
        serializer.save(business=business)


class PostDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET: public (AllowAny), looked up by pk.
    PATCH/DELETE: require auth AND an explicit object-level ownership
    check inside perform_update()/perform_destroy() — defense in depth,
    not left to permission_classes alone (same as ProductDetailView).
    DELETE is the inherited soft delete (is_deleted=True), never a real
    row deletion.
    """

    serializer_class = PostSerializer
    queryset = Post.objects.all()

    def get_permissions(self):
        if self.request.method in ("PATCH", "PUT", "DELETE"):
            return [permissions.IsAuthenticated()]
        return [permissions.AllowAny()]

    def get_object(self):
        post = _get_post_or_404(self.kwargs["pk"])
        self.check_object_permissions(self.request, post)
        return post

    def _check_ownership(self, post):
        if post.business.user_id != self.request.user.id:
            raise PermissionDenied("You do not own this post.")

    def perform_update(self, serializer):
        self._check_ownership(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._check_ownership(instance)
        instance.delete()


class PostPublicListView(generics.ListAPIView):
    """
    Part P-043. GET /api/v1/posts/public/ — public, read-only list of
    approved-and-visible Posts, optionally narrowed to one business via
    ?business_id= — the first endpoint a Customer with no account at
    all can actually use to browse content.

    Sourced from Post.published_objects (never Post.objects) — this is
    the entire point of this part: a pending or rejected Post, or a
    soft-deleted-but-approved one, must never appear here, even if
    requested directly by id (there is no detail route for this view;
    PostDetailView's own public GET is unaffected and unchanged, and is
    a separate, pre-existing concern from P-041).

    ?business_id= filter and StandardCursorPagination both copy
    ProductPublicListView's exact pattern (products/views.py, P-032),
    per this part's own spec.
    """

    serializer_class = PostPublicSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        queryset = Post.published_objects.all()
        business_id = self.request.query_params.get("business_id")
        if business_id is not None:
            queryset = queryset.filter(business_id=business_id)
        return queryset


class ReelListCreateView(generics.ListCreateAPIView):
    """
    Part P-042. Same ownership/list-create pattern as PostListCreateView
    above, byte-for-byte, with exactly one addition in perform_create():
    dispatching content.tasks.transcode_reel via .delay() once the raw
    upload is saved. The Reel is created with processing_status=
    "uploaded" (the model field's own default) and — because
    Reel.auto_enqueue_on_create is False — with NO ModerationQueue row
    yet; the dispatched task is what eventually creates that row, once
    (and only if) transcoding reaches "ready". See content/tasks.py and
    content/models.py's Reel docstring for the full mechanism.
    """

    serializer_class = ReelSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        business = getattr(self.request.user, "business_profile", None)
        if business is None:
            return Reel.objects.none()
        return Reel.objects.filter(business=business)

    def perform_create(self, serializer):
        business = getattr(self.request.user, "business_profile", None)
        if business is None:
            raise PermissionDenied(
                "Only an authenticated business account can create reels."
            )
        reel = serializer.save(business=business)
        transcode_reel.delay(reel.id)


class ReelDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    Part P-042. Same public-GET / owner-only-PATCH-DELETE pattern as
    PostDetailView above, byte-for-byte. See ReelSerializer's docstring
    for the one known, deliberately out-of-scope limitation: PATCHing
    `video` here does not re-trigger transcode_reel.
    """

    serializer_class = ReelSerializer
    queryset = Reel.objects.all()

    def get_permissions(self):
        if self.request.method in ("PATCH", "PUT", "DELETE"):
            return [permissions.IsAuthenticated()]
        return [permissions.AllowAny()]

    def get_object(self):
        reel = _get_reel_or_404(self.kwargs["pk"])
        self.check_object_permissions(self.request, reel)
        return reel

    def _check_ownership(self, reel):
        if reel.business.user_id != self.request.user.id:
            raise PermissionDenied("You do not own this reel.")

    def perform_update(self, serializer):
        self._check_ownership(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._check_ownership(instance)
        instance.delete()


class ReelPublicListView(generics.ListAPIView):
    """
    Part P-043. GET /api/v1/reels/public/ — same shape as
    PostPublicListView above, sourced from Reel.published_objects
    (ReelPublishedManager — also requires processing_status="ready",
    see content/models.py). Same ?business_id= filter, same
    StandardCursorPagination.
    """

    serializer_class = ReelPublicSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        queryset = Reel.published_objects.all()
        business_id = self.request.query_params.get("business_id")
        if business_id is not None:
            queryset = queryset.filter(business_id=business_id)
        return queryset