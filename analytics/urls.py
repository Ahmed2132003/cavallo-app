from django.urls import path

from analytics.views import BusinessDailyStatsListView
from analytics.dashboard_views import (
    BusinessContentDetailView,
    BusinessContentExternalMetricsView,
    BusinessContentListView,
    BusinessOrdersSummaryView,
    BusinessOverviewView,
    BusinessProductDetailView,
    BusinessProductListView,
)
from analytics.event_views import RecordViewEventView

urlpatterns = [
    path("events/views/", RecordViewEventView.as_view(), name="view-event-record"),
    path(
        "business/<int:pk>/overview/",
        BusinessOverviewView.as_view(),
        name="business-analytics-overview",
    ),
    path(
        "business/<int:pk>/products/",
        BusinessProductListView.as_view(),
        name="business-analytics-products",
    ),
    path(
        "business/<int:pk>/products/<int:product_id>/",
        BusinessProductDetailView.as_view(),
        name="business-analytics-product-detail",
    ),
    path(
        "business/<int:pk>/content/",
        BusinessContentListView.as_view(),
        name="business-analytics-content",
    ),
    path(
        "business/<int:pk>/content/<str:content_type>/<int:content_id>/",
        BusinessContentDetailView.as_view(),
        name="business-analytics-content-detail",
    ),
    path(
        (
            "business/<int:pk>/content/"
            "<str:content_type>/<int:content_id>/external-metrics/"
        ),
        BusinessContentExternalMetricsView.as_view(),
        name="business-analytics-content-external",
    ),
    path(
        "business/<int:pk>/orders-summary/",
        BusinessOrdersSummaryView.as_view(),
        name="business-analytics-orders-summary",
    ),
    path(
        "business/<int:pk>/daily/",
        BusinessDailyStatsListView.as_view(),
        name="business-daily-stats",
    ),
]
