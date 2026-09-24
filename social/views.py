"""
Views for Part P-052 — Follow/Unfollow (idempotent) + atomic
denormalized counters.

This is the first genuine application of architecture Section 5 rule
4 (never COUNT() a live table) under real concurrent-write risk. Both
counters (BusinessProfile.follower_count, User.following_count) are
mutated EXCLUSIVELY via .filter(pk=...).update(field=F(field) + 1/-1)
— never instance.field = instance.field + 1 followed by .save(),
which races under concurrent requests (classic read-then-write bug).

Idempotency contract:
- POST (follow): following an already-followed business returns 200
  with no duplicate Follow row and no double-increment.
- DELETE (unfollow): unfollowing a business you don't follow is a
  harmless no-op — still 200, never an error, counters untouched.

Assumption (flagged, per this part's own spec instruction): the
architecture doesn't say whether Business-type accounts may follow
other businesses. This view allows ANY authenticated user regardless
of account_type — see social/models.py's Follow docstring.
"""

from django.db import transaction
from django.db.models import F, Q
from django.contrib.auth import get_user_model
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.views.decorators.csrf import csrf_exempt

from core.permissions import HasCapability

from businesses.models import BusinessProfile

from .models import Comment, Follow, Like, Save

User = get_user_model()


def _get_business_or_404(pk):
    # Mirrors content/views.py's _get_post_or_404 /
    # stories/views.py's _get_story_or_404 convention — a plain
    # DRF-NotFound helper, not a queryset-level 404 (keeps the error
    # in this project's standard {"error": {...}} envelope, not a raw
    # Django Http404 with an HTML body).
    try:
        return BusinessProfile.objects.get(pk=pk)
    except BusinessProfile.DoesNotExist:
        raise NotFound("Business not found.")


class FollowToggleView(APIView):
    """
    POST   /api/v1/businesses/{id}/follow/ — follow (idempotent)
    DELETE /api/v1/businesses/{id}/follow/ — unfollow (idempotent)
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        business = _get_business_or_404(pk)

        with transaction.atomic():
            _follow, created = Follow.objects.get_or_create(
                follower=request.user, business=business
            )
            if created:
                BusinessProfile.objects.filter(pk=business.pk).update(
                    follower_count=F("follower_count") + 1
                )
                User.objects.filter(pk=request.user.pk).update(
                    following_count=F("following_count") + 1
                )

        return Response({"following": True})

    def delete(self, request, pk):
        business = _get_business_or_404(pk)

        with transaction.atomic():
            deleted_count, _ = Follow.objects.filter(
                follower=request.user, business=business
            ).delete()
            if deleted_count:
                BusinessProfile.objects.filter(
                    pk=business.pk, follower_count__gt=0
                ).update(follower_count=F("follower_count") - 1)
                User.objects.filter(pk=request.user.pk, following_count__gt=0).update(
                    following_count=F("following_count") - 1
                )

        return Response({"following": False})


from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from rest_framework.exceptions import ValidationError

# Whitelist of content_type strings this endpoint accepts, mapped to
# (app_label, model_name). Deliberately explicit and closed — NOT
# derived from ContentType.objects.all() — so an unrecognized or
# unintended model (e.g. "user", "businessprofile") can never be
# liked just because it exists in the ContentType table.
ALLOWED_CONTENT_TYPES = {
    "post": ("content", "Post"),
    "reel": ("content", "Reel"),
}


def _resolve_like_target(content_type_str, object_id):
    if content_type_str not in ALLOWED_CONTENT_TYPES:
        raise ValidationError(
            {
                "content_type": (
                    f"Unrecognized content_type '{content_type_str}'. "
                    f"Must be one of: {', '.join(ALLOWED_CONTENT_TYPES)}."
                )
            }
        )
    app_label, model_name = ALLOWED_CONTENT_TYPES[content_type_str]
    model = apps.get_model(app_label, model_name)
    try:
        obj = model.objects.get(pk=object_id)
    except model.DoesNotExist:
        raise NotFound(f"{model_name} not found.")
    content_type = ContentType.objects.get_for_model(model)
    return content_type, obj


class LikeToggleView(APIView):
    """
    POST   /api/v1/likes/ — like (idempotent)
    DELETE /api/v1/likes/ — unlike (idempotent)

    Body: {"content_type": "post"|"reel", "object_id": <id>}

    Mirrors FollowToggleView's exact transactional shape (P-052):
    get_or_create()/filter().delete() gated atomic F()-counter update,
    resolved generically via obj.__class__ instead of a fixed model.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        content_type_str = request.data.get("content_type")
        object_id = request.data.get("object_id")
        if not content_type_str or object_id is None:
            raise ValidationError(
                {"detail": "Both 'content_type' and 'object_id' are required."}
            )
        content_type, obj = _resolve_like_target(content_type_str, object_id)

        with transaction.atomic():
            _like, created = Like.objects.get_or_create(
                user=request.user, content_type=content_type, object_id=obj.pk
            )
            if created:
                obj.__class__.objects.filter(pk=obj.pk).update(
                    likes_count=F("likes_count") + 1
                )

        return Response({"liked": True})

    def delete(self, request):
        content_type_str = request.data.get("content_type")
        object_id = request.data.get("object_id")
        if not content_type_str or object_id is None:
            raise ValidationError(
                {"detail": "Both 'content_type' and 'object_id' are required."}
            )
        content_type, obj = _resolve_like_target(content_type_str, object_id)

        with transaction.atomic():
            deleted_count, _ = Like.objects.filter(
                user=request.user, content_type=content_type, object_id=obj.pk
            ).delete()
            if deleted_count:
                obj.__class__.objects.filter(pk=obj.pk, likes_count__gt=0).update(
                    likes_count=F("likes_count") - 1
                )

        return Response({"liked": False})


# Whitelist for Part P-054 (Save) — separate from LikeToggleView's
# ALLOWED_CONTENT_TYPES above because Save includes "product" and Like
# does not (P-053 stays Post/Reel-only; see that view's own comment).
# Kept deliberately explicit and closed, same reasoning as Like's.
SAVE_ALLOWED_CONTENT_TYPES = {
    "post": ("content", "Post"),
    "reel": ("content", "Reel"),
    "product": ("products", "Product"),
}


def _resolve_save_target(content_type_str, object_id):
    if content_type_str not in SAVE_ALLOWED_CONTENT_TYPES:
        raise ValidationError(
            {
                "content_type": (
                    f"Unrecognized content_type '{content_type_str}'. "
                    f"Must be one of: {', '.join(SAVE_ALLOWED_CONTENT_TYPES)}."
                )
            }
        )
    app_label, model_name = SAVE_ALLOWED_CONTENT_TYPES[content_type_str]
    model = apps.get_model(app_label, model_name)
    try:
        obj = model.objects.get(pk=object_id)
    except model.DoesNotExist:
        raise NotFound(f"{model_name} not found.")
    content_type = ContentType.objects.get_for_model(model)
    return content_type, obj


class SaveToggleView(APIView):
    """
    POST   /api/v1/saves/ — save (idempotent)
    DELETE /api/v1/saves/ — unsave (idempotent)

    Body: {"content_type": "post"|"reel"|"product", "object_id": <id>}

    Same idempotent-toggle shape as Follow (P-052) and Like (P-053),
    minus any counter update — Save is a private, per-user bookmark
    only; no public saves_count exists anywhere on any target model.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        content_type_str = request.data.get("content_type")
        object_id = request.data.get("object_id")
        if not content_type_str or object_id is None:
            raise ValidationError(
                {"detail": "Both 'content_type' and 'object_id' are required."}
            )
        content_type, obj = _resolve_save_target(content_type_str, object_id)

        Save.objects.get_or_create(
            user=request.user, content_type=content_type, object_id=obj.pk
        )

        return Response({"saved": True})

    def delete(self, request):
        content_type_str = request.data.get("content_type")
        object_id = request.data.get("object_id")
        if not content_type_str or object_id is None:
            raise ValidationError(
                {"detail": "Both 'content_type' and 'object_id' are required."}
            )
        content_type, obj = _resolve_save_target(content_type_str, object_id)

        Save.objects.filter(
            user=request.user, content_type=content_type, object_id=obj.pk
        ).delete()

        return Response({"saved": False})


from rest_framework.generics import ListAPIView

from core.pagination import StandardCursorPagination

from .serializers import (
    CommentCreateSerializer,
    CommentListQuerySerializer,
    CommentSerializer,
    SaveSerializer,
)


class SaveListView(ListAPIView):
    """
    GET /api/v1/saves/me/ — the authenticated user's own saved items,
    spanning Post/Reel/Product, cursor-paginated per P-011's
    convention. Structurally IDOR-safe: always scoped to
    request.user, no id parameter exists to manipulate (same
    singleton "/me/" pattern as businesses/me/, P-026).
    """

    permission_classes = [IsAuthenticated]
    serializer_class = SaveSerializer
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        return Save.objects.filter(user=self.request.user).select_related(
            "content_type"
        )


# Whitelist for Part P-055 (Comment) — its own explicit, closed
# whitelist, separate from Like's and Save's (same reasoning as
# SAVE_ALLOWED_CONTENT_TYPES above). Stories have no comments
# (architecture-mandated), and Product is not commentable in the MVP.
COMMENT_ALLOWED_CONTENT_TYPES = {
    "post": ("content", "Post"),
    "reel": ("content", "Reel"),
}


def _resolve_comment_target(content_type_str, object_id):
    """
    Returns (ContentType, model, obj). Unlike Like/Save, the target
    must be PUBLISHED (published_objects: status == published, not
    soft-deleted, and for Reel also processing_status == ready) —
    commenting on pending/rejected/deleted content is never valid.
    """
    if content_type_str not in COMMENT_ALLOWED_CONTENT_TYPES:
        raise ValidationError(
            {
                "content_type": (
                    f"Unrecognized content_type '{content_type_str}'. "
                    f"Must be one of: {', '.join(COMMENT_ALLOWED_CONTENT_TYPES)}."
                )
            }
        )
    app_label, model_name = COMMENT_ALLOWED_CONTENT_TYPES[content_type_str]
    model = apps.get_model(app_label, model_name)
    try:
        obj = model.published_objects.get(pk=object_id)
    except model.DoesNotExist:
        raise NotFound(f"{model_name} not found.")
    return ContentType.objects.get_for_model(model), model, obj


class CommentCreateView(APIView):
    """
    POST /api/v1/comments/ — create a comment (Part P-055).

    Body: {"content_type": "post"|"reel", "object_id": <id>, "text": "..."}

    DELIBERATELY NO MODERATION: the comment is live the instant this
    view returns. Nothing here touches apps/moderation — Comment is the
    one confirmed, documented exception to the Moderatable pattern (see
    social/models.py's Comment docstring). Moderation is reactive only
    (Report + auto-hide threshold, Part P-057 / social/services.py).

    comments_count uses the same pattern as P-052/P-053: an atomic
    `.filter(pk=...).update(comments_count=F("comments_count") + 1)`
    inside the same transaction.atomic() as the insert.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        content_type, model, obj = _resolve_comment_target(
            data["content_type"], data["object_id"]
        )

        with transaction.atomic():
            comment = Comment.objects.create(
                user=request.user,
                content_type=content_type,
                object_id=obj.pk,
                text=data["text"],
            )
            model.objects.filter(pk=obj.pk).update(
                comments_count=F("comments_count") + 1
            )

        return Response(CommentSerializer(comment).data, status=201)


# Reuses P-019's capability factory (same check the moderation API uses)
# to decide whether a viewer may see hidden comments. Imported from
# core.permissions, NOT from apps/moderation — Comment stays fully
# decoupled from the moderation app.
_CanModerateContent = HasCapability("can_moderate_content")


class CommentListView(ListAPIView):
    """
    GET /api/v1/comments/?content_type=post|reel&object_id=<id> — public,
    cursor-paginated (newest first) list of a Post/Reel's comments
    (Part P-055).

    Visibility of auto-hidden comments (is_hidden=True):
      - anonymous / unrelated authenticated user -> NOT included
      - the comment's own author                 -> included
      - holder of can_moderate_content           -> included
    Soft-deleted comments are never included for anyone
    (Comment.objects excludes them).
    """

    permission_classes = [AllowAny]
    serializer_class = CommentSerializer
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        query = CommentListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        content_type, _model, obj = _resolve_comment_target(
            query.validated_data["content_type"],
            query.validated_data["object_id"],
        )

        queryset = Comment.objects.filter(
            content_type=content_type, object_id=obj.pk
        ).select_related("content_type")

        user = self.request.user
        if user.is_authenticated:
            if _CanModerateContent().has_permission(self.request, self):
                return queryset
            return queryset.filter(Q(is_hidden=False) | Q(user=user))
        return queryset.filter(is_hidden=False)


_comment_list_view = CommentListView.as_view()
_comment_create_view = CommentCreateView.as_view()


@csrf_exempt
def comment_collection_view(request, *args, **kwargs):
    """
    Single-URL dispatcher for /api/v1/comments/: reads go to
    CommentListView (public), everything else to CommentCreateView
    (authenticated POST; other methods get its 405).

    @csrf_exempt is REQUIRED: DRF's APIView.as_view() marks its own
    callable csrf_exempt, but this plain wrapper is what the URLconf
    resolves to, so Django's CsrfViewMiddleware would otherwise apply
    to POSTs. (JWT auth is not cookie-based; DRF enforces CSRF itself
    only for SessionAuthentication.)
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return _comment_list_view(request, *args, **kwargs)
    return _comment_create_view(request, *args, **kwargs)
