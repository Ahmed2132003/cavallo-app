from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from chat.models import Conversation, ConversationParticipant
from chat.serializers import ConversationSerializer

User = get_user_model()


class ConversationStartView(APIView):
    """
    POST /api/v1/conversations/start/
    Body: {"recipient_id": <int>}

    Resolves to an existing conversation between request.user and the
    recipient if one exists, or creates a new one plus both
    ConversationParticipant rows. No account_type check of any kind —
    any two authenticated accounts can start a conversation, per the
    confirmed any-to-any decision.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        recipient_id = request.data.get("recipient_id")
        if not recipient_id:
            return Response(
                {"detail": "recipient_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if str(recipient_id) == str(request.user.id):
            return Response(
                {"detail": "Cannot start a conversation with yourself."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        recipient = User.objects.filter(id=recipient_id).first()
        if recipient is None:
            return Response(
                {"detail": "Recipient not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        existing = (
            Conversation.objects.filter(participants__user=request.user)
            .filter(participants__user=recipient)
            .distinct()
            .first()
        )
        if existing is not None:
            return Response(
                ConversationSerializer(existing).data,
                status=status.HTTP_200_OK,
            )

        with transaction.atomic():
            conversation = Conversation.objects.create()
            ConversationParticipant.objects.create(
                conversation=conversation, user=request.user
            )
            ConversationParticipant.objects.create(
                conversation=conversation, user=recipient
            )

        return Response(
            ConversationSerializer(conversation).data,
            status=status.HTTP_201_CREATED,
        )
