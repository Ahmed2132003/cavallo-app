from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers

from content.models import Post, Reel
from core.media import validate_upload
from moderation.models import ModerationLog, ModerationQueue
from social.models import Like, Save


def _get_is_liked(obj, context):
    """
    Part BUGFIX-058. True iff request.user has an active Like row on
    this object. Returns False for an unauthenticated/anonymous
    request rather than querying with a null user — the public
    endpoints this serializer backs (PostPublicListView,
    ReelPublicListView, and the home feed via FeedItemSerializer) are
    reachable without auth, and must not leak or error in that case.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    content_type = ContentType.objects.get_for_model(obj.__class__)
    return Like.objects.filter(
        user=user, content_type=content_type, object_id=obj.pk
    ).exists()


def _get_is_saved(obj, context):
    """Part BUGFIX-058. Same shape as _get_is_liked, for social.models.Save."""
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    content_type = ContentType.objects.get_for_model(obj.__class__)
    return Save.objects.filter(
        user=user, content_type=content_type, object_id=obj.pk
    ).exists()


def _get_rejection_reason(obj):
    """
    Part P-044. Return the reason text from the most recent 'rejected'
    ModerationLog entry for this Moderatable object, or None if the
    object is not currently rejected (or — defensively, should not
    normally happen once status == "rejected" — no such log exists
    yet). Used exclusively by the owner-facing PostSerializer/
    ReelSerializer below.

    Deliberately NOT added to PostPublicSerializer/ReelPublicSerializer
    (P-043) — those classes exist specifically to keep moderation
    metadata off the unauthenticated public endpoint; a rejected object
    never appears there anyway (published_objects only), so the field
    would be dead weight there at best.
    """
    if obj.status != obj.Status.REJECTED:
        return None

    content_type = ContentType.objects.get_for_model(obj.__class__)
    queue_item = (
        ModerationQueue.objects.filter(content_type=content_type, object_id=obj.id)
        .order_by("-created_at")
        .first()
    )
    if queue_item is None:
        return None

    log = (
        ModerationLog.objects.filter(
            queue_item=queue_item, action=ModerationLog.Action.REJECTED
        )
        .order_by("-created_at")
        .first()
    )
    return log.reason if log else None


class PostSerializer(serializers.ModelSerializer):
    """
    Write fields: caption, image. Everything else (id, business, status,
    timestamps) is read-only — `business` is resolved server-side from
    request.user.business_profile in the view (P-032's exact pattern),
    and `status` is exclusively managed by moderation.services.approve()/
    reject(), never writable through this app's own serializer.

    `rejection_reason` (Part P-044): a SerializerMethodField, always
    read-only by construction (no need to list it in read_only_fields).
    Populated only when status == "rejected"; None otherwise.
    """

    rejection_reason = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = (
            "id",
            "business",
            "caption",
            "image",
            "status",
            "rejection_reason",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "business", "status", "created_at", "updated_at")

    def validate_image(self, value):
        if value:
            validate_upload(
                value,
                allowed_mime_types=["image/jpeg", "image/png", "image/webp"],
                max_size_bytes=5 * 1024 * 1024,
            )
        return value

    def get_rejection_reason(self, obj):
        return _get_rejection_reason(obj)


class ReelSerializer(serializers.ModelSerializer):
    """
    Part P-042. Write fields: caption, video — that is all. `thumbnail`,
    `duration_seconds` and `processing_status` are read-only here
    because exactly one thing is allowed to set them:
    content.tasks.transcode_reel(). Letting a client PATCH any of the
    three directly would let it fake a "ready" Reel with a fabricated
    thumbnail/duration that never actually went through ffmpeg.

    `business` and `status` are read-only for the exact same reasons as
    PostSerializer above (business resolved server-side in the view;
    status exclusively owned by moderation.services).

    `rejection_reason` (Part P-044): same SerializerMethodField as
    PostSerializer above — populated only when status == "rejected".

    Known, deliberately out-of-scope limitation (flag for a future
    part, not silently "fixed" here): PATCHing `video` on an existing
    Reel replaces the file but does NOT re-trigger transcode_reel, so
    thumbnail/duration_seconds/processing_status would silently go
    stale against the new file. This part's spec only covers the
    create-time pipeline; re-transcoding on update was never in scope.
    """

    rejection_reason = serializers.SerializerMethodField()

    class Meta:
        model = Reel
        fields = (
            "id",
            "business",
            "caption",
            "video",
            "thumbnail",
            "duration_seconds",
            "processing_status",
            "status",
            "rejection_reason",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "business",
            "thumbnail",
            "duration_seconds",
            "processing_status",
            "status",
            "created_at",
            "updated_at",
        )

    def validate_video(self, value):
        # Same P-013 content-sniffing choke point as PostSerializer's
        # validate_image — real MIME detection via libmagic, not the
        # filename extension or client-supplied Content-Type. 100 MB
        # is a placeholder ceiling (this part's spec gives no exact
        # number); revisit once real product limits are decided.
        validate_upload(
            value,
            allowed_mime_types=["video/mp4", "video/quicktime", "video/webm"],
            max_size_bytes=100 * 1024 * 1024,
        )
        return value

    def get_rejection_reason(self, obj):
        return _get_rejection_reason(obj)


class PostPublicSerializer(serializers.ModelSerializer):
    """
    Part P-043. Read-only, public-facing representation of a Post —
    used exclusively by PostPublicListView (GET /api/v1/posts/public/).

    Deliberately a SEPARATE class from PostSerializer above, not a
    reused/subset config of it: this class exists specifically to keep
    internal moderation metadata (the `status` field — and, by
    extension, any future rejection-reason field) off the one endpoint
    a Customer with no auth at all can hit. Every row PostPublicListView
    ever returns already has status="published" by construction (via
    Post.published_objects), so re-exposing that field here would only
    ever show one constant value while creating a place a future
    moderation-metadata field could leak through by accident.

    Part BUGFIX-058: adds `is_liked`/`is_saved` (per-request-user,
    SerializerMethodField, False when unauthenticated) and
    `likes_count`/`comments_count`/`shares_count` (the existing
    denormalized counters already on the Post model -- no migration
    needed). Fixes the cross-account like/save state leak documented
    in cavallo-mobile's ContentActionRow "KNOWN GAP".
    """

    is_liked = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = (
            "id",
            "business",
            "caption",
            "image",
            "likes_count",
            "comments_count",
            "shares_count",
            "is_liked",
            "is_saved",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_is_liked(self, obj):
        return _get_is_liked(obj, self.context)

    def get_is_saved(self, obj):
        return _get_is_saved(obj, self.context)


class ReelPublicSerializer(serializers.ModelSerializer):
    """
    Part P-043. Read-only, public-facing representation of a Reel —
    used exclusively by ReelPublicListView (GET /api/v1/reels/public/).

    Same rationale as PostPublicSerializer above, plus one more
    exclusion specific to Reel: `processing_status` is also left off.
    Every row ReelPublicListView ever returns already has
    processing_status="ready" by construction (via
    Reel.published_objects / ReelPublishedManager), so — exactly like
    `status` — it would only ever show one constant value here while
    needlessly exposing an internal pipeline-state field to an
    unauthenticated caller.

    Part BUGFIX-058: adds `is_liked`/`is_saved` (per-request-user,
    SerializerMethodField, False when unauthenticated) and
    `likes_count`/`comments_count`/`shares_count` (the existing
    denormalized counters already on the Reel model -- no migration
    needed). Fixes the cross-account like/save state leak documented
    in cavallo-mobile's ContentActionRow "KNOWN GAP".
    """

    is_liked = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()

    class Meta:
        model = Reel
        fields = (
            "id",
            "business",
            "caption",
            "video",
            "thumbnail",
            "duration_seconds",
            "likes_count",
            "comments_count",
            "shares_count",
            "is_liked",
            "is_saved",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_is_liked(self, obj):
        return _get_is_liked(obj, self.context)

    def get_is_saved(self, obj):
        return _get_is_saved(obj, self.context)