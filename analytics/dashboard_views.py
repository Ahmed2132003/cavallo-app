"""
Part BI-02 - Owner-only dashboard APIs.

Check order (same as the P-084 daily endpoint): 401 -> 404 (no such business)
-> 403 (not the owner) -> 400 (bad query). The business always comes from the
URL id and is checked for ownership; product / content ids are always looked
up INSIDE that business (another merchant's id is a plain 404). Any
business_id in the query string or body is ignored.
"""

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics import external, metrics
from analytics.dashboard_serializers import (
    ContentListQuerySerializer,
    DateRangeQuerySerializer,
    ExternalMetricInputSerializer,
    OrdersSummaryQuerySerializer,
    ProductListQuerySerializer,
)
from analytics.models import ExternalContentMetricSnapshot
from analytics.permissions import IsAnalyticsBusinessOwner
from analytics.utils import iso
from businesses.models import BusinessProfile
from django.utils import timezone
from products.models import Product


class BusinessAnalyticsBaseView(APIView):
    permission_classes = [IsAnalyticsBusinessOwner]

    def get_business(self, request, pk):
        business = BusinessProfile.objects.filter(pk=pk).first()
        if business is None:
            raise NotFound("Business not found.")
        self.check_object_permissions(request, business)
        return business

    @staticmethod
    def parse_query(serializer_class, request):
        serializer = serializer_class(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def get_content_item(self, business, content_type, content_id):
        item = metrics.get_content_item(business, content_type, content_id)
        if item is None:
            raise NotFound("Content not found.")
        return item


class BusinessOverviewView(BusinessAnalyticsBaseView):
    """GET /api/v1/analytics/business/{id}/overview/"""

    def get(self, request, pk):
        business = self.get_business(request, pk)
        params = self.parse_query(DateRangeQuerySerializer, request)
        date_from, date_to, label = metrics.resolve_range(
            params, metrics.default_date_to(business)
        )
        current = metrics.build_overview(business, date_from, date_to)
        previous = metrics.build_previous_period(business, date_from, date_to, current)
        return Response(
            {
                "business_id": business.pk,
                "period": metrics.period_dict(date_from, date_to, label),
                "overview": current["overview"],
                "funnel": current["funnel"],
                "previous_period": previous,
                "coverage": current["coverage"],
                "data_updated_at": current["data_updated_at"],
                "orders_data_updated_at": current["orders_data_updated_at"],
            }
        )


class BusinessProductListView(BusinessAnalyticsBaseView):
    """GET /api/v1/analytics/business/{id}/products/"""

    def get(self, request, pk):
        business = self.get_business(request, pk)
        params = self.parse_query(ProductListQuerySerializer, request)
        key, descending = metrics.parse_ordering(
            params.get("ordering"), metrics.PRODUCT_ORDERING, "-views"
        )
        date_from, date_to, label = metrics.resolve_range(
            params, metrics.default_date_to(business)
        )
        cov = metrics.rollup_coverage(business, date_from, date_to)
        rows = metrics.build_product_rows(business, date_from, date_to, cov)
        rows = metrics.sort_rows(rows, key, descending)
        page = metrics.paginate(rows, params["page"], params["page_size"])
        return Response(
            {
                "business_id": business.pk,
                "period": metrics.period_dict(date_from, date_to, label),
                **page,
                "coverage": metrics.public_coverage(cov),
                "data_updated_at": iso(cov["updated"]),
            }
        )


class BusinessProductDetailView(BusinessAnalyticsBaseView):
    """GET /api/v1/analytics/business/{id}/products/{product_id}/"""

    def get(self, request, pk, product_id):
        business = self.get_business(request, pk)
        product = Product.objects.filter(pk=product_id, business=business).first()
        if product is None:
            raise NotFound("Product not found.")
        params = self.parse_query(DateRangeQuerySerializer, request)
        date_from, date_to, label = metrics.resolve_range(
            params, metrics.default_date_to(business)
        )
        data = metrics.build_product_detail(business, product, date_from, date_to)
        data["period"]["period"] = label
        return Response(data)


class BusinessContentListView(BusinessAnalyticsBaseView):
    """GET /api/v1/analytics/business/{id}/content/?type=post|reel|all"""

    def get(self, request, pk):
        business = self.get_business(request, pk)
        params = self.parse_query(ContentListQuerySerializer, request)
        key, descending = metrics.parse_ordering(
            params.get("ordering"), metrics.CONTENT_ORDERING, "-views"
        )
        date_from, date_to, label = metrics.resolve_range(
            params, metrics.default_date_to(business)
        )
        kinds = ("post", "reel") if params["type"] == "all" else (params["type"],)
        cov = metrics.rollup_coverage(business, date_from, date_to)
        rows = metrics.build_content_rows(business, kinds, date_from, date_to, cov)
        rows = metrics.sort_rows(rows, key, descending)
        page = metrics.paginate(rows, params["page"], params["page_size"])
        return Response(
            {
                "business_id": business.pk,
                "type": params["type"],
                "period": metrics.period_dict(date_from, date_to, label),
                **page,
                "coverage": metrics.public_coverage(cov),
                "data_updated_at": iso(cov["updated"]),
            }
        )


class BusinessContentDetailView(BusinessAnalyticsBaseView):
    """GET /api/v1/analytics/business/{id}/content/{type}/{id}/"""

    def get(self, request, pk, content_type, content_id):
        business = self.get_business(request, pk)
        item = self.get_content_item(business, content_type, content_id)
        params = self.parse_query(DateRangeQuerySerializer, request)
        date_from, date_to, label = metrics.resolve_range(
            params, metrics.default_date_to(business)
        )
        data = metrics.build_content_detail(
            business, content_type, item, date_from, date_to
        )
        data["period"]["period"] = label
        return Response(data)


class BusinessContentExternalMetricsView(BusinessAnalyticsBaseView):
    """
    GET  .../content/{type}/{id}/external-metrics/  latest value per
         (platform, metric_type) with source and last_updated.
    POST same URL: the owner types in a number (source is always "manual").
    Snapshots accumulate; nothing is updated or scraped.
    """

    def get(self, request, pk, content_type, content_id):
        business = self.get_business(request, pk)
        item = self.get_content_item(business, content_type, content_id)
        return Response(
            {
                "business_id": business.pk,
                "content_type": content_type,
                "content_id": item.pk,
                "metrics": external.latest_snapshots(business, content_type, item.pk),
                "providers_registered": external.registered_platforms(),
            }
        )

    def post(self, request, pk, content_type, content_id):
        business = self.get_business(request, pk)
        item = self.get_content_item(business, content_type, content_id)
        serializer = ExternalMetricInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        snapshot = external.create_snapshot(
            business=business,
            content_type=content_type,
            content_id=item.pk,
            platform=data["platform"],
            metric_type=data["metric_type"],
            metric_value=data["metric_value"],
            captured_at=data.get("captured_at") or timezone.now(),
            source=ExternalContentMetricSnapshot.Source.MANUAL,
            external_reference=data.get("external_reference", ""),
        )
        return Response(
            external.serialize_snapshot(snapshot), status=status.HTTP_201_CREATED
        )


class BusinessOrdersSummaryView(BusinessAnalyticsBaseView):
    """GET /api/v1/analytics/business/{id}/orders-summary/ (live from orders)"""

    def get(self, request, pk):
        business = self.get_business(request, pk)
        params = self.parse_query(OrdersSummaryQuerySerializer, request)
        date_from, date_to, label = metrics.resolve_range(params, timezone.localdate())
        data = metrics.build_orders_summary(business, date_from, date_to, params["top"])
        data["period"]["period"] = label
        return Response(data)
