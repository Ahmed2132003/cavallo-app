"""
Moderator queue API (Part P-038).

Thin HTTP wrappers around moderation.services (P-037): NO state
transition logic lives here. approve()/reject() remain the only code
that moves content to published/rejected.

Every endpoint is gated by HasCapability("can_moderate_content")
(P-019); there are no manual role checks.

The services raise django.core.exceptions.ValidationError, which the
P-012 exception handler does not recognise (it only reshapes DRF
exceptions), so it would surface as a 500. _run_decision() translates:
    AlreadyDecidedError        -> ConflictError (409, code CONFLICT)
    any other ValidationError  -> DRF ValidationError (400)
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import ConflictError
from core.pagination import StandardCursorPagination
from core.permissions import HasCapability
from moderation import services
from moderation.models import ModerationQueue
from moderation.serializers import (
    ModerationQueueSerializer,
    RejectRequestSerializer,
)

CanModerateContent = HasCapability("can_moderate_content")


def _first_message(exc):
    messages = getattr(exc, "messages", None)
    return messages[0] if messages else str(exc)


def _get_queue_item_or_404(pk):
    """
    Resolve a queue row or raise DRF's NotFound (code NOT_FOUND).

    Deliberately not django.shortcuts.get_object_or_404: that raises
    Django's Http404, which the P-012 handler cannot map to a code (it
    would come out as "ERROR"). Same convention as products/views.py.
    """
    try:
        return ModerationQueue.objects.get(pk=pk)
    except ModerationQueue.DoesNotExist:
        raise NotFound("Moderation queue item not found.")


def _run_decision(action, *args):
    """Call a moderation service and map its errors to API errors."""
    try:
        return action(*args)
    except services.AlreadyDecidedError as exc:
        raise ConflictError(_first_message(exc))
    except DjangoValidationError as exc:
        raise ValidationError(_first_message(exc))


class ModerationQueueListView(ListAPIView):
    """
    GET /api/v1/moderation/queue/

    Pending items only, cursor-paginated (newest first). Optional
    ``?priority=normal|fast_path`` narrows the list (e.g. a moderator
    clearing time-sensitive Story reviews first).
    """

    permission_classes = [CanModerateContent]
    serializer_class = ModerationQueueSerializer
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        queryset = (
            ModerationQueue.objects.filter(status=ModerationQueue.Status.PENDING)
            .select_related("content_type")
            .prefetch_related("content_object")
        )

        priority = self.request.query_params.get("priority")
        if priority:
            valid = ModerationQueue.Priority.values
            if priority not in valid:
                raise ValidationError(
                    {
                        "priority": [
                            f"Invalid priority. Valid values: {', '.join(valid)}."
                        ]
                    }
                )
            queryset = queryset.filter(priority=priority)

        return queryset


class ApproveView(APIView):
    """POST /api/v1/moderation/queue/{id}/approve/"""

    permission_classes = [CanModerateContent]

    def post(self, request, pk):
        queue_item = _get_queue_item_or_404(pk)
        _run_decision(services.approve, queue_item, request.user)
        return Response(ModerationQueueSerializer(queue_item).data)


class RejectView(APIView):
    """POST /api/v1/moderation/queue/{id}/reject/  body: {"reason": "..."}"""

    permission_classes = [CanModerateContent]

    def post(self, request, pk):
        queue_item = _get_queue_item_or_404(pk)

        body = RejectRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        _run_decision(
            services.reject,
            queue_item,
            request.user,
            body.validated_data["reason"],
        )
        return Response(ModerationQueueSerializer(queue_item).data)
