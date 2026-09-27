import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from chat.consumers import presence_cache_key
from chat.models import Conversation, ConversationParticipant, Message
from chat.serializers import ConversationSerializer, MessageSerializer
from chat.tasks import notify_offline_recipient

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


def _require_participant(conversation_id, user):
    """
    نفس فحص الـ IDOR المستخدم في كل view في الملف ده — مستخدم مسجّل
    دخول لكنه مش ConversationParticipant في الـ conversation المستهدف
    بيترفض بـ 403. مسحوبة لدالة مشتركة هنا لأن MessageSendView
    وMessageFetchSinceView (P-072) بقى عندهم نفس الفحص بالظبط.
    """
    is_participant = ConversationParticipant.objects.filter(
        conversation_id=conversation_id, user=user
    ).exists()
    if not is_participant:
        raise PermissionDenied("You are not a participant of this conversation.")


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

    Part P-068 (persistence + best-effort broadcast) + Part P-072
    (best-effort offline-push dispatch, added below the broadcast step).

    من P-072: هذا الـ view دلوقتي بيوصله بس الـ POST requests، عن طريق
    message_collection_view dispatcher تحت في آخر الملف ده — نفس الـ
    pattern المستخدم في social/views.py's comment_collection_view.
    الـ GET requests على نفس الـ URL بتوصل لـ MessageFetchSinceView.

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
    ولا على الـ 201 response اللي الـ client استلمه بالفعل.

    STEP 3 (OFFLINE PUSH DISPATCH — best-effort, Part P-072): بعد
    محاولة البرودكاست (بغض النظر عن نجاحها)، بنحدد المستلم (المشارك
    التاني في الـ Conversation غير الـ sender) وبنشوف هل هو is_online
    دلوقتي — بنفس presence_cache_key() المستخدم في P-070's
    UserPresenceView، عشان مايتكررش الـ "online:{id}" string literal في
    مكان تالت. لو مش متصل، بنعمل notify_offline_recipient.delay(message.id)
    (P-072). لو متصل، مانعملش حاجة — هو بيستقبل الرسالة live عن طريق
    البرودكاست فعلاً، وأي push هنا هيبقى إزعاج مكرر. الـ dispatch نفسه
    ملفوف في try/except منفصل — نفس فلسفة البرودكاست بالظبط: فشل هنا
    يتسجل ويتجاهل، ومايأثرش على الـ 201 response.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id):
        conversation = _get_conversation_or_404(conversation_id)
        _require_participant(conversation_id, request.user)

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

        # Part P-072 — best-effort offline-push dispatch. Runs
        # regardless of whether the broadcast above succeeded or
        # failed (the broadcast's own try/except already swallowed
        # any error), and is itself wrapped the same way: a failure
        # here must never turn the already-successful 201 into an
        # error.
        try:
            recipient_participant = (
                ConversationParticipant.objects.filter(
                    conversation_id=conversation_id
                )
                .exclude(user_id=request.user.id)
                .first()
            )
            if recipient_participant is not None:
                recipient_id = recipient_participant.user_id
                is_recipient_online = (
                    cache.get(presence_cache_key(recipient_id)) is not None
                )
                if not is_recipient_online:
                    notify_offline_recipient.delay(message.id)
        except Exception:
            logger.exception(
                "Best-effort offline-push dispatch failed for "
                "conversation_id=%s message_id=%s; message is already "
                "persisted and the HTTP response is unaffected.",
                conversation_id,
                message.id,
            )

        return Response(response_data, status=status.HTTP_201_CREATED)


class MessageFetchSinceView(APIView):
    """
    GET /api/v1/conversations/<conversation_id>/messages/?since=<message_id>

    Part P-072 — "fetch-unread-on-reconnect": بيرجع كل الـ Messages في
    الـ conversation اللي id بتاعها أكبر من `since` (مرتبة زمنيًا
    تصاعديًا، زي Message.Meta.ordering أصلاً)، عشان reconnecting client
    يقدر يلحّق أي رسالة اتبعتت وقت ما كان offline.

    `since` بيتاخد كـ message id (id__gt) مش كـ timestamp: Message.id
    auto-increment ومرتب فعليًا مع وقت الإنشاء (نفس ordering الموديل)،
    فده أبسط وأنضف من التعامل مع دقة/timezone لـ timestamp، ومطابق
    لصيغة الـ spec ("since={message_id_or_timestamp} — أيهما أنضف").
    لو محتجنا مستقبلًا دعم timestamp كمان، ده تغيير إضافي منفصل مش
    كسر لحاجة موجودة.

    نفس فحص IDOR المستخدم في MessageSendView أعلاه بالظبط
    (_require_participant): مستخدم مش participant في الـ conversation
    بيترفض بـ 403، مش مجرد "مش مسجل دخول". نفس الـ 404 المستخدم في كل
    مكان في الملف ده لـ conversation id مش موجود أصلاً.

    `since` إجباري (query param): طلب من غيره أو بقيمة مش integer
    بيترفض بـ 400 — نفس أسلوب التحقق الصريح المستخدم في feed/views.py
    وsearch/views.py (خطأ 400 واضح، مش 500 غامض).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id):
        _get_conversation_or_404(conversation_id)
        _require_participant(conversation_id, request.user)

        since_raw = request.query_params.get("since")
        if since_raw is None:
            raise ValidationError({"since": "This query parameter is required."})
        try:
            since_id = int(since_raw)
        except ValueError:
            raise ValidationError({"since": "Must be an integer message id."})

        messages = Message.objects.filter(
            conversation_id=conversation_id, id__gt=since_id
        )
        serializer = MessageSerializer(messages, many=True)
        return Response(serializer.data)


_message_send_view = MessageSendView.as_view()
_message_fetch_since_view = MessageFetchSinceView.as_view()


@csrf_exempt
def message_collection_view(request, *args, **kwargs):
    """
    Single-URL dispatcher for /api/v1/conversations/<conversation_id>/
    messages/ (Part P-072): GET goes to MessageFetchSinceView
    (fetch-since-reconnect), everything else goes to MessageSendView
    (POST send; other methods get its 405). Exact same pattern as
    social/views.py's comment_collection_view (Part P-055) — one URL,
    method-based dispatch to two separate APIView classes, rather than
    cramming both get()/post() into a single class.

    @csrf_exempt is REQUIRED for the same reason documented on
    comment_collection_view: APIView.as_view() marks its own callable
    csrf_exempt, but this plain function wrapper is what the URLconf
    actually resolves to, so Django's CsrfViewMiddleware would
    otherwise apply to POSTs. JWT auth here is not cookie-based, so
    this is safe (DRF only enforces CSRF itself for
    SessionAuthentication).
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return _message_fetch_since_view(request, *args, **kwargs)
    return _message_send_view(request, *args, **kwargs)


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