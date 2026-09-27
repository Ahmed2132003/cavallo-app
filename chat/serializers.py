from rest_framework import serializers


from .models import Conversation, ConversationParticipant, Message


class ConversationSerializer(serializers.ModelSerializer):
    participant_ids = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ["id", "created_at", "updated_at", "participant_ids"]

    def get_participant_ids(self, obj):
        return list(obj.participants.values_list("user_id", flat=True))


class MessageSerializer(serializers.ModelSerializer):
    """
    `text` هو الحقل الوحيد القابل للكتابة من الـ client. `conversation`
    و`sender` بيتحددوا من الـ view (من الـ URL kwarg ومن request.user
    على التوالي)، مش من جسم الـ request — بنفس منطق IDOR المتبع في
    باقي الأجزاء (لا نثق بأي معرّف يبعته الـ client لتحديد الهوية أو
    العلاقة). `status` بياخد قيمته الافتراضية 'sent' من الـ Model نفسه.
    """

    class Meta:
        model = Message
        fields = ["id", "conversation", "sender", "text", "status", "created_at"]
        read_only_fields = ["id", "conversation", "sender", "status", "created_at"]


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
