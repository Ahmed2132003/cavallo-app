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

from rest_framework.exceptions import NotFound
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
    SubscriptionMeSerializer,
)
from monetization.services import (
    calculate_custom_price,
    custom_quota_bytes,
    effective_state,
    get_subscription,
)
from monetization.storage_usage import get_used_bytes


class PlanListView(ListAPIView):
    queryset = Plan.objects.filter(is_active=True).order_by(
        "sort_order", "duration_days", "id"
    )
    serializer_class = PlanSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    pagination_class = None


class SubscriptionMeView(APIView):
    """
    BE-06. GET /api/v1/monetization/subscription/me/ - the merchant's own
    storage-plan subscription. Merchant accounts only (IsBusinessAccount),
    deliberately NOT gated by HasActiveSubscription: a locked merchant must
    see their state to renew. Owner resolved from request.user only.
    No business profile / no subscription row -> 404.
    """

    permission_classes = [IsBusinessAccount]

    def get(self, request):
        business = getattr(request.user, "business_profile", None)
        if business is None:
            raise NotFound("Business profile not found.")
        sub = get_subscription(business)
        if sub is None:
            raise NotFound("No subscription found.")
        state = effective_state(business)
        payload = {
            "plan": sub.plan,
            "status": sub.status,
            "state": state["state"],
            "days_left": state["days_left"],
            "trial_ends_at": sub.trial_ends_at,
            "current_period_end": sub.current_period_end,
            "purge_at": state["purge_at"],
            "quota_bytes": sub.quota_bytes,
            "used_bytes": get_used_bytes(business),
            "is_featured_effective": sub.is_featured_effective,
            "pending_plan": sub.pending_plan,
            "locked": state["locked"],
        }
        return Response(SubscriptionMeSerializer(payload).data)


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
