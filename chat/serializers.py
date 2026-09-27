from rest_framework import serializers

from chat.models import Conversation, Message


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
