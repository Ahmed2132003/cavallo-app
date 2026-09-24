from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from reports.serializers import ReportCreateSerializer
from reports.services import submit_report
from reports.targets import resolve_report_target
from reports.throttles import ReportRateThrottle


class ReportCreateView(APIView):
    """POST /api/v1/reports/

    Body: {"content_type": "comment|post|reel|story|product|business",
           "object_id": <id>, "reason": "spam|inappropriate|misleading|other",
           "details": "<optional text>"}

    201 {"reported": true} for a new report, 200 {"reported": true} if this
    user already reported the same target. Rate-limited by its own dedicated
    throttle scope ("report").
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ReportRateThrottle]

    def post(self, request):
        serializer = ReportCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        target = resolve_report_target(data["content_type"], data["object_id"])
        _report, created = submit_report(
            reporter=request.user,
            target=target,
            reason=data["reason"],
            details=data["details"],
        )
        return Response(
            {"reported": True},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
