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

from monetization.models import Plan
from monetization.serializers import PlanSerializer


class PlanListView(ListAPIView):
    queryset = Plan.objects.order_by("duration_days", "id")
    serializer_class = PlanSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    pagination_class = None
