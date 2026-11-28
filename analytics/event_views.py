from rest_framework.response import Response
from rest_framework.views import APIView

from analytics.event_serializers import ViewEventSerializer
from analytics.permissions import CanRecordViewEvent
from analytics.throttles import ViewEventAnonThrottle, ViewEventUserThrottle
from analytics.tracking import record_view_event


class RecordViewEventView(APIView):
    """
    Part BI-02. POST /api/v1/analytics/events/views/

    Logged in or anonymous (CanRecordViewEvent), throttled per IP for
    anonymous callers and per user for logged-in ones. POST only: PATCH, PUT
    and DELETE return 405, and no request body can ever set a counter.

    201 recorded | 200 duplicate (same event_id) | 200 ignored (reel below
    the minimum watch time) | 400 validation | 403 owner viewing own item |
    404 target missing or not active | 429 throttled.
    """

    permission_classes = [CanRecordViewEvent]
    throttle_classes = [ViewEventAnonThrottle, ViewEventUserThrottle]

    def post(self, request):
        serializer = ViewEventSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        status_code, body = record_view_event(
            user=request.user, data=serializer.validated_data
        )
        return Response(body, status=status_code)
