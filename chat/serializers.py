from rest_framework import serializers

from chat.models import Conversation


class ConversationSerializer(serializers.ModelSerializer):
    participant_ids = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ["id", "created_at", "updated_at", "participant_ids"]

    def get_participant_ids(self, obj):
        return list(obj.participants.values_list("user_id", flat=True))
