from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from chat.models import Conversation, ConversationParticipant
from chat.serializers import ConversationSerializer, MessageSerializer

User = get_user_model()


def _get_conversation_or_404(conversation_id):
    """
    DRF NotFound صراحةً، مش django.shortcuts.get_object_or_404 — طبقًا
    لقاعدة P-038، عشان الـ error envelope يطلع code=NOT_FOUND مش
    الـ ERROR العام. نفس الـ convention المستخدم في
    content/views.py's _get_post_or_404 / _get_reel_or_404.
    """
    conversation = Conversation.objects.filter(pk=conversation_id).first()
    if conversation is None:
        raise NotFound("Conversation not found.")
    return conversation


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


class MessageSendView(APIView):
    """
    POST /api/v1/conversations/<conversation_id>/messages/

    Part P-068 — STEP 1 من 2 — PERSISTENCE فقط.

    بيحفظ Message في Postgres (status='sent' من الـ default بتاع
    الـ Model نفسه) عن طريق ORM call متزامن عادي جوه الـ request cycle
    العادي بتاع Django REST — ويرجع 201 فورًا بمجرد ما الحفظ يخلص.
    هذه هي النص الأول من القاعدة المعمارية الأهم ("احفظ قبل ما تبعت
    broadcast") اللي STEP 1 بيثبته لوحده: الـ HTTP response مربوط
    فقط بنجاح الحفظ.

    STEP 2 هيضيف خطوة الـ broadcast الاختيارية (best-effort) عن طريق
    channel_layer.group_send على f"conversation_{id}"، ملفوفة بحيث
    فشلها ميأثرش على هذا الـ response، بالإضافة لـ chat_message handler
    في ChatConsumer. STEP 1 عن قصد مش بيستورد ولا بيلمس channels
    نهائيًا، عشان نقدر نثبت النصين بشكل مستقل، زي ما الـ Definition
    of Done بتاع الـ Part نفسه بيطلب.

    نفس انضباط الـ IDOR المستخدم في P-067's WebSocket consumer's
    connect() check: مستخدم مسجّل دخول لكنه مش ConversationParticipant
    في الـ conversation المستهدف بيترفض بـ 403 — مش مجرد "مش مسجل دخول".
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id):
        conversation = _get_conversation_or_404(conversation_id)

        is_participant = ConversationParticipant.objects.filter(
            conversation_id=conversation_id, user=request.user
        ).exists()
        if not is_participant:
            raise PermissionDenied("You are not a participant of this conversation.")

        serializer = MessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = serializer.save(conversation=conversation, sender=request.user)

        return Response(
            MessageSerializer(message).data,
            status=status.HTTP_201_CREATED,
        )
