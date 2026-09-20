from rest_framework import generics, permissions
from rest_framework.exceptions import NotFound, PermissionDenied

from content.models import Post
from content.serializers import PostSerializer
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