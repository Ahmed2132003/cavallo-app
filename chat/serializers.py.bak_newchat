from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from core.media import validate_upload
from social.serializers import _preview_for

from .models import Conversation, ConversationParticipant, Message

# ---------------------------------------------------------------------------
# Part P-076 — chat media limits (documented, single source of truth).
#
# Images: jpeg/png/webp, 5 MB — identical to Post.image / Product.image.
# Video:  video/mp4 only, 25 MB. Chat video is NOT transcoded (no ffmpeg
#         pipeline like Reel's), so the stricter 25 MB cap (vs Reel's
#         100 MB) is the chosen alternative to transcoding. To also accept
#         iPhone .mov later, add "video/quicktime" to CHAT_VIDEO_MIME_TYPES.
# ---------------------------------------------------------------------------
CHAT_IMAGE_MIME_TYPES = ["image/jpeg", "image/png", "image/webp"]
CHAT_IMAGE_MAX_BYTES = 5 * 1024 * 1024
CHAT_VIDEO_MIME_TYPES = ["video/mp4"]
CHAT_VIDEO_MAX_BYTES = 25 * 1024 * 1024

# ---------------------------------------------------------------------------
# Part P-077 — shared content (Post / Reel / Product) inside a message.
#
# Closed whitelist, same philosophy as social/views.py's
# ALLOWED_CONTENT_TYPES / SHARE_ALLOWED_CONTENT_TYPES: never derived from
# ContentType.objects.all(), so a model like "user" can never be shared just
# because it exists. Unlike P-056's Share-tracking endpoint (post/reel only),
# chat sharing also accepts "product" — the Product detail screen's
# "Share to conversation" needs it.
# ---------------------------------------------------------------------------
SHARE_TO_CHAT_CONTENT_TYPES = {
    "post": ("content", "Post"),
    "reel": ("content", "Reel"),
    "product": ("products", "Product"),
}


def _shareable_queryset(model):
    """
    Only content that is publicly visible right now may be shared or
    rendered: Post/Reel via their published_objects manager (P-043),
    Product via is_active=True (Product.objects already hides
    soft-deleted rows). Same visibility rules as the public endpoints.
    """
    published = getattr(model, "published_objects", None)
    if published is not None:
        return published.all()
    return model.objects.filter(is_active=True)


def resolve_shareable_target(content_type_str, object_id):
    """
    Resolve a (content_type string, id) pair to (ContentType, instance),
    the same way P-053's _resolve_like_target does: unknown type ->
    ValidationError (400), missing/unpublished/inactive target ->
    NotFound (404).
    """
    if content_type_str not in SHARE_TO_CHAT_CONTENT_TYPES:
        raise serializers.ValidationError(
            {
                "shared_content_type": (
                    f"Unrecognized shared_content_type '{content_type_str}'. "
                    f"Must be one of: {', '.join(SHARE_TO_CHAT_CONTENT_TYPES)}."
                )
            }
        )
    app_label, model_name = SHARE_TO_CHAT_CONTENT_TYPES[content_type_str]
    model = apps.get_model(app_label, model_name)
    target = _shareable_queryset(model).filter(pk=object_id).first()
    if target is None:
        raise NotFound(f"{model_name} not found.")
    return ContentType.objects.get_for_model(model), target


def shared_content_type_label(message):
    """ "post" / "reel" / "product", or "" when nothing is shared."""
    if message.shared_content_type_id is None:
        return ""
    return ContentType.objects.get_for_id(message.shared_content_type_id).model


def build_shared_content_payload(message):
    """
    Viewer-INDEPENDENT description of a message's shared content, or None
    when the message shares nothing. Deliberately carries no per-viewer
    state (no is_liked / is_saved): the same payload is returned by REST
    and broadcast over the WebSocket to the other participant. The Flutter
    client fetches the live, per-viewer entity by (content_type, object_id)
    and renders PostCard / ReelCard with it.

    `available` is False when the target was unpublished / deactivated /
    deleted after being shared; `preview` and the business fields are then
    null so a taken-down item's details never keep leaking through chat.
    Cost: one query per shared message (documented MVP limitation, same
    class as ConversationListSerializer's N+1).
    """
    label = shared_content_type_label(message)
    if not label:
        return None

    payload = {
        "content_type": label,
        "object_id": message.shared_object_id,
        "available": False,
        "business_id": None,
        "business_name": None,
        "preview": None,
    }
    model = ContentType.objects.get_for_id(message.shared_content_type_id).model_class()
    if model is None:
        return payload
    target = (
        _shareable_queryset(model)
        .select_related("business")
        .filter(pk=message.shared_object_id)
        .first()
    )
    if target is None:
        return payload

    payload.update(
        available=True,
        business_id=target.business_id,
        business_name=target.business.business_name,
        preview=_preview_for(target),
    )
    return payload


class ConversationSerializer(serializers.ModelSerializer):
    participant_ids = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ["id", "created_at", "updated_at", "participant_ids"]

    def get_participant_ids(self, obj):
        return list(obj.participants.values_list("user_id", flat=True))


class MessageSerializer(serializers.ModelSerializer):
    """
    `text` و`media` هما الحقلان القابلان للكتابة من الـ client (كلاهما
    اختياري، لكن لازم واحد منهما على الأقل). `conversation` و`sender`
    بيتحددوا من الـ view، و`media_type` بيتحدد هنا من نوع الملف الحقيقي
    (libmagic عبر validate_upload) — مش من أي حاجة يبعتها الـ client.

    Part P-076: التحقق من الملف بيستخدم core.media.validate_upload()
    نفسها بدون أي منطق تحقق بديل. ملحوظة: validate_upload بتفحص الحجم
    قبل النوع، فلو استخدمنا سقف الصورة (5MB) مباشرة هيترفض فيديو 10MB
    غلط. عشان كده الفحص على مرحلتين، كلهم عن طريق نفس الدالة:
      1) نوع صورة + سقف الفيديو (25MB) — نجح => صورة، وبعدها نطبق سقف
         الصورة (5MB) بنفس الدالة.
      2) لو النوع مش صورة => نوع فيديو + سقف الفيديو.
    """

    # Part P-077 — write side: two plain inputs (type string + id) resolved
    # server-side by resolve_shareable_target(); read side: `shared_content`
    # (viewer-independent payload, see build_shared_content_payload()).
    # These two declared fields intentionally shadow the model's own
    # shared_content_type / shared_object_id columns of the same name.
    shared_content_type = serializers.CharField(write_only=True, required=False)
    shared_object_id = serializers.IntegerField(
        write_only=True, required=False, min_value=1
    )
    shared_content = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "conversation",
            "sender",
            "text",
            "media",
            "media_type",
            "shared_content_type",
            "shared_object_id",
            "shared_content",
            "status",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "conversation",
            "sender",
            "media_type",
            "status",
            "created_at",
        ]

    def validate_media(self, value):
        try:
            validate_upload(value, CHAT_IMAGE_MIME_TYPES, CHAT_VIDEO_MAX_BYTES)
        except DjangoValidationError as exc:
            if exc.code != "unsupported_file_type":
                # file_too_large (أكبر من 25MB أيًا كان النوع) — نرفضه كما هو.
                raise
            # مش صورة: يا فيديو مسموح، يا يترفض بنفس رسالة validate_upload.
            validate_upload(value, CHAT_VIDEO_MIME_TYPES, CHAT_VIDEO_MAX_BYTES)
            self._media_type = Message.MediaType.VIDEO
        else:
            # صورة حقيقية: نطبق سقف الصورة (5MB) عبر نفس الدالة.
            validate_upload(value, CHAT_IMAGE_MIME_TYPES, CHAT_IMAGE_MAX_BYTES)
            self._media_type = Message.MediaType.IMAGE
        return value

    def validate(self, attrs):
        text = (attrs.get("text") or "").strip()
        media = attrs.get("media")

        # Part P-077 — shared content: the type string and the id must
        # come together, may accompany text, but never media.
        has_shared_type = "shared_content_type" in attrs
        has_shared_id = "shared_object_id" in attrs
        if has_shared_type != has_shared_id:
            raise serializers.ValidationError(
                "shared_content_type and shared_object_id must be sent together."
            )
        has_shared = has_shared_type and has_shared_id
        if has_shared and media is not None:
            raise serializers.ValidationError(
                "A message cannot carry both media and shared content."
            )

        if not text and media is None and not has_shared:
            raise serializers.ValidationError(
                "A message must contain text, media, shared content, "
                "or text together with shared content."
            )
        if media is not None:
            attrs["media_type"] = self._media_type
        if has_shared:
            content_type, _target = resolve_shareable_target(
                attrs["shared_content_type"], attrs["shared_object_id"]
            )
            # Replace the client's string with the resolved ContentType
            # instance so serializer.save() writes the real FK column.
            attrs["shared_content_type"] = content_type
        return attrs

    def get_shared_content(self, obj):
        return build_shared_content_payload(obj)


def _resolve_conversation_participant_display_name(user):
    """
    Resolve a display name for a user in a chat context, regardless of
    account_type. Reasonably resolved via whichever profile data is
    available (Business or Customer) — per P-074's Detailed Implementation.

    Uses getattr() with a default rather than a bare attribute access:
    Django's reverse-OneToOne descriptor raises a DoesNotExist that is
    also an AttributeError subclass specifically so this pattern works
    (same convention already used for the moderation "submitter" object).
    """
    business_profile = getattr(user, "business_profile", None)
    if business_profile is not None:
        return business_profile.business_name

    customer_profile = getattr(user, "customer_profile", None)
    if customer_profile is not None:
        return customer_profile.display_name

    return user.email


class ConversationListSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for the Conversation List screen (P-074).

    Named distinctly from the plain `ConversationSerializer` above (which
    `ConversationStartView` already returns, with its `participant_ids`
    shape) — this is a different, additive representation for a different
    screen, not a replacement.

    NOTE (documented limitation, not silently accepted): last_message and
    unread_count each run a small extra query per conversation (N+1).
    Acceptable at MVP scale (a user's conversation count is small); revisit
    with Prefetch objects if this list ever needs to scale to hundreds of
    conversations per user.

    Part P-076: last_message now also carries `media_type` (additive) so
    the list can show a "Photo"/"Video" preview for a media-only message
    whose `text` is blank.
    """

    other_participant = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "other_participant",
            "last_message",
            "unread_count",
            "created_at",
        ]
        read_only_fields = fields

    def get_other_participant(self, obj):
        request = self.context["request"]
        participant = (
            ConversationParticipant.objects.filter(conversation=obj)
            .exclude(user=request.user)
            .select_related("user")
            .first()
        )
        if participant is None:
            # Defensive only — every Conversation always has exactly 2
            # participants per P-066's creation flow; should never happen.
            return None
        user = participant.user
        return {
            "id": user.id,
            "account_type": user.account_type,
            "display_name": _resolve_conversation_participant_display_name(user),
        }

    def get_last_message(self, obj):
        message = (
            Message.objects.filter(conversation=obj).order_by("-created_at").first()
        )
        if message is None:
            return None
        return {
            "id": message.id,
            "text": message.text,
            "media_type": message.media_type,
            "shared_content_type": shared_content_type_label(message),
            "sender_id": message.sender_id,
            "status": message.status,
            "created_at": message.created_at,
        }

    def get_unread_count(self, obj):
        request = self.context["request"]
        return (
            Message.objects.filter(conversation=obj)
            .exclude(sender=request.user)
            .exclude(status="read")
            .count()
        )
