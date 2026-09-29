from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from core.media import validate_upload

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

    class Meta:
        model = Message
        fields = [
            "id",
            "conversation",
            "sender",
            "text",
            "media",
            "media_type",
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
        if not text and media is None:
            raise serializers.ValidationError(
                "A message must contain text, media, or both."
            )
        if media is not None:
            attrs["media_type"] = self._media_type
        return attrs


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
