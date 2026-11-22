"""
Part P-092 (STEP 1): public, read-only Plan list.

GET /api/v1/monetization/plans/

WHY PUBLIC: the Web Dashboard (ADR-006) must show Featured pricing
before the visitor has logged in, so this endpoint needs no
authentication. ``authentication_classes = []`` (same pattern as
businesses.views.BusinessProfilePublicView) means a stale or garbage
Authorization header is ignored instead of turning a public read into a
401.

WHY IT IS SAFE: Plan rows are admin-managed catalogue data (name,
duration, price, currency) with nothing user-specific in them. The view
is list-only (ListAPIView), so POST/PUT/PATCH/DELETE return 405 and a
Plan can only ever be created or changed in Django Admin.

NOT PAGINATED: there is a handful of Plans, and the dashboard needs the
whole list to render pricing, so the response is a plain JSON array.
"""

from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from monetization.models import Plan
from monetization.permissions import IsBusinessAccount
from monetization.serializers import (
    CustomQuoteRequestSerializer,
    CustomQuoteResponseSerializer,
    PlanSerializer,
)
from monetization.services import calculate_custom_price, custom_quota_bytes


class PlanListView(ListAPIView):
    queryset = Plan.objects.filter(is_active=True).order_by(
        "sort_order", "duration_days", "id"
    )
    serializer_class = PlanSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    pagination_class = None


class CustomQuoteView(APIView):
    """
    BE-03: price quote for a custom Infinity plan. Merchant accounts only.

    GET  /api/v1/monetization/subscriptions/custom-quote/?gb=20
    POST /api/v1/monetization/subscriptions/custom-quote/  {"gb": 20}

    Read-only: creates nothing and never touches a subscription, so a
    merchant whose subscription is locked/expired can still get a price.
    Invalid / out-of-range gb -> 400 (P-012 envelope, field "gb").
    """

    permission_classes = [IsBusinessAccount]

    def get(self, request):
        return self._quote(request.query_params)

    def post(self, request):
        return self._quote(request.data)

    def _quote(self, data):
        serializer = CustomQuoteRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        gb = serializer.validated_data["gb"]
        payload = {
            "gb": gb,
            "price_egp": calculate_custom_price(gb),
            "quota_bytes": custom_quota_bytes(gb),
            "is_featured": True,
        }
        return Response(CustomQuoteResponseSerializer(payload).data)
