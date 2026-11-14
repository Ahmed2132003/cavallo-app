from django.urls import path

from orders.views import (
    CustomerOrderDetailView,
    CustomerOrderListView,
    MerchantOrderDetailView,
    MerchantOrderEventListView,
    MerchantOrderListView,
    MerchantOrderNoteView,
    MerchantOrderStatusView,
    MerchantProductOrderListView,
    OrderFromChatView,
)

app_name = "orders"

urlpatterns = [
    path("from-chat/", OrderFromChatView.as_view(), name="order-from-chat"),
    path("my/", CustomerOrderListView.as_view(), name="customer-order-list"),
    path(
        "my/<int:pk>/", CustomerOrderDetailView.as_view(), name="customer-order-detail"
    ),
    path(
        "by-product/<int:product_id>/",
        MerchantProductOrderListView.as_view(),
        name="merchant-product-orders",
    ),
    path(
        "<int:pk>/status/",
        MerchantOrderStatusView.as_view(),
        name="merchant-order-status",
    ),
    path(
        "<int:pk>/notes/",
        MerchantOrderNoteView.as_view(),
        name="merchant-order-notes",
    ),
    path(
        "<int:pk>/events/",
        MerchantOrderEventListView.as_view(),
        name="merchant-order-events",
    ),
    path("<int:pk>/", MerchantOrderDetailView.as_view(), name="merchant-order-detail"),
    path("", MerchantOrderListView.as_view(), name="merchant-order-list"),
]
