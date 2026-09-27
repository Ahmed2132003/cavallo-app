import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from chat.consumers import presence_cache_key
from chat.models import Conversation, ConversationParticipant
from chat.serializers import ConversationSerializer, MessageSerializer

User = get_user_model()
logger = logging.getLogger(__name__)


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

    Part P-068 — الجزءان الاثنان كاملين الآن.

    STEP 1 (PERSISTENCE): بيحفظ Message في Postgres (status='sent' من
    الـ default بتاع الـ Model نفسه) عن طريق ORM call متزامن عادي جوه
    الـ request cycle العادي بتاع Django REST. هذا الحفظ هو الوحيد
    اللي بيحدد نجاح أو فشل الـ HTTP response — أي exception هنا لازم
    يفشل الـ request فعلاً (السلوك الافتراضي، لسه زي ما هو).

    STEP 2 (BROADCAST — best-effort): بعد ما الحفظ يخلص ويتجهز الـ 201
    response، بنحاول channel_layer.group_send() على
    f"conversation_{conversation_id}" — العملية دي ملفوفة في
    try/except خاص بيها لوحدها، بحيث أي فشل فيها (channel layer واقع،
    Redis مش راضي يرد، أو حتى mock بيفشّلها عمدًا في التيست) يتسجّل في
    الـ log ويتم تجاهله تمامًا، وما يأثرش لا على الـ Message المحفوظة
    ولا على الـ 201 response اللي الـ client استلمه بالفعل. دي بالظبط
    القاعدة المعمارية المذكورة صراحةً: "احفظ الأول، ابعت الإشعار بعدين
    كخطوة مستقلة، وفشل الإشعار مايهمش الحفظ خالص."

    ChatConsumer (P-067) عندها الآن chat_message handler method بيستقبل
    الحدث ده ("type": "chat.message" — Channels بتحوّل النقطة لـ
    underscore عند مناداة الـ handler، يعني chat.message -> chat_message)
    وبتبعت الرسالة كـ JSON للـ WebSocket client المتصل.

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

        # الحفظ فوق خلص بالفعل ونجح — من هنا لغاية آخر الدالة، أي حاجة
        # تفشل ميرجعش عليها HTTP error ولا يترجع الـ Message تتمسح.
        response_data = MessageSerializer(message).data

        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"conversation_{conversation_id}",
                {
                    "type": "chat.message",
                    "message": response_data,
                },
            )
        except Exception:
            # عمدًا bare except: أي نوع فشل ممكن يحصل هنا (network,
            # serialization, layer misconfiguration, mocked failure في
            # التيست) هو "broadcast فشل" بالنسبة للـ contract بتاع الـ
            # part ده — والاستجابة الوحيدة المطلوبة هي: سجّل ولا تعمل
            # أي حاجة تانية. الرسالة هتوصل على أي حال في أول fetch أو
            # reconnect للـ conversation (P-072).
            logger.exception(
                "Best-effort WebSocket broadcast failed for "
                "conversation_id=%s message_id=%s; message is already "
                "persisted and the HTTP response is unaffected.",
                conversation_id,
                message.id,
            )

        return Response(response_data, status=status.HTTP_201_CREATED)


class UserPresenceView(APIView):
    """
    GET /api/v1/conversations/users/<int:user_id>/presence/

    Part P-070 (STEP 2) -- the REST half of Redis-backed presence.
    Reads the exact same cache key ChatConsumer (chat/consumers.py)
    sets on connect()/heartbeat and clears on disconnect(), via the
    shared presence_cache_key() helper, so the "online:{id}" key
    format can never drift out of sync between the two halves of this
    part -- there is exactly one place that string literal is defined.

    Architecture decision (flagged explicitly, per this project's own
    "don't silently decide, flag it" convention -- see e.g. P-070's
    own spec on last-seen tracking): the Part P-070 spec names this
    endpoint GET /api/v1/users/{id}/presence/, but no `users` app and
    no /api/v1/users/ prefix exist anywhere in config/urls.py. Every
    route this project has ever added that concerns the User model
    itself lives under /api/v1/auth/ (accounts.urls: register/login/
    logout/me/) -- there is no general-purpose "look up any user by
    id" namespace at all, by design, since most of this project's
    IDOR discipline (P-026 onward) has been about NOT exposing other
    users' data behind arbitrary ids. Presence is chat-specific data
    (it exists only because ChatConsumer tracks it) and the requesting
    client that needs it is specifically the Flutter chat screen (per
    the spec's own "checked on-demand by the Flutter chat screen"),
    so this endpoint is routed under chat's own existing prefix
    instead of introducing a brand-new top-level /api/v1/users/
    namespace for a single one-off route:

        GET /api/v1/conversations/users/<int:user_id>/presence/

    Same HTTP method, same response semantics the spec describes
    (does the presence key currently exist), just under the URL
    namespace that actually exists. If a real standalone `users`
    concept is ever introduced later, this route can move under it
    without changing anything else about this part.

    Authorization: IsAuthenticated only -- deliberately NOT restricted
    to "user_id must be a participant of a conversation shared with
    request.user". Flagging this rather than silently deciding it:
    the spec places no such restriction on this check (unlike message
    send/receive, which the WebSocket consumer's connect() already
    scopes to conversation participants), and a bare online/offline
    boolean for a user id that's already discoverable elsewhere (e.g.
    inside ConversationSerializer's own participant_ids, or any
    public business profile id) is a much lower-sensitivity read than
    message content or profile data. Revisit this if a future part
    wants presence hidden from non-contacts.

    404s for a user_id that doesn't exist at all -- via the same
    explicit DRF NotFound convention every other view in this project
    already uses (see _get_conversation_or_404 above) -- rather than
    silently reporting a made-up id as "offline" the same way a real
    but-not-currently-connected user would be reported.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, user_id):
        if not User.objects.filter(id=user_id).exists():
            raise NotFound("User not found.")

        is_online = cache.get(presence_cache_key(user_id)) is not None
        return Response({"user_id": user_id, "is_online": is_online})
