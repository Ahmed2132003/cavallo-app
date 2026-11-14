"""
Part BI-01 — HTTP layer for orders.

Merchant endpoints resolve the business from request.user
(services.get_owner_business) and then check object ownership explicitly
inside each view (order.business_id == business.id -> otherwise 404). A
merchant therefore cannot read or change another merchant's order, and the
response does not reveal whether such an order exists.

Customer endpoints only ever see orders where order.customer == request.user.

Lists use StandardCursorPagination (project pattern; ordering assumed to be
-created_at, as for the other paginated lists in the project).
"""

from django.utils.dateparse import parse_date
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardCursorPagination
from products.models import Product

from .constants import OrderStatus, parse_order_number
from .models import Order
from .serializers import (
    CustomerOrderDetailSerializer,
    CustomerOrderSerializer,
    MerchantOrderEventSerializer,
    MerchantOrderSerializer,
    OrderFromChatSerializer,
    OrderNoteSerializer,
    OrderStatusChangeSerializer,
)
from .services import (
    add_order_note,
    change_order_status,
    create_order_from_chat,
    get_owner_business,
)

_MAX_ID = 2**63 - 1


def _merchant_orders():
    return Order.objects.select_related(
        "customer",
        "customer__customer_profile",
        "customer__business_profile",
        "product",
    )


def _get_merchant_order(order_id, business):
    order = _merchant_orders().filter(pk=order_id).first()
    # Explicit object-level ownership check (defense in depth).
    if order is None or order.business_id != business.id:
        raise NotFound("Order not found.")
    return order


def _int_param(params, name):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({name: ["Must be an integer."]})
    if value < 1 or value > _MAX_ID:
        raise ValidationError({name: ["Must be a positive integer."]})
    return value


def _date_param(params, name):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        value = parse_date(raw)
    except ValueError:
        value = None
    if value is None:
        raise ValidationError({name: ["Must be a date in YYYY-MM-DD format."]})
    return value


def _status_param(params):
    value = params.get("status")
    if value in (None, ""):
        return None
    if value not in OrderStatus.values:
        raise ValidationError(
            {"status": [f"Must be one of: {', '.join(OrderStatus.values)}."]}
        )
    return value


def _apply_merchant_filters(queryset, params):
    status_value = _status_param(params)
    if status_value:
        queryset = queryset.filter(status=status_value)
    for param, lookup in (
        ("product", "product_id"),
        ("customer", "customer_id"),
        ("conversation", "conversation_id"),
    ):
        value = _int_param(params, param)
        if value is not None:
            queryset = queryset.filter(**{lookup: value})
    # Dates are UTC calendar dates of created_at (settings.TIME_ZONE = UTC).
    date_from = _date_param(params, "date_from")
    if date_from:
        queryset = queryset.filter(created_at__date__gte=date_from)
    date_to = _date_param(params, "date_to")
    if date_to:
        queryset = queryset.filter(created_at__date__lte=date_to)
    search = params.get("search")
    if search not in (None, ""):
        order_id = parse_order_number(search)
        queryset = queryset.none() if order_id is None else queryset.filter(pk=order_id)
    return queryset


class OrderFromChatView(APIView):
    """
    POST /api/v1/orders/from-chat/ — 201 when created, 200 with the SAME
    order when the message already has one.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = OrderFromChatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order, created = create_order_from_chat(
            user=request.user, **serializer.validated_data
        )
        order = _merchant_orders().get(pk=order.pk)
        return Response(
            MerchantOrderSerializer(order).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MerchantOrderListView(generics.ListAPIView):
    """
    GET /api/v1/orders/ — the business's own orders.
    Filters: status, product, customer, conversation, date_from, date_to,
    search (order number: 42, #42 or ORD-42).
    """

    serializer_class = MerchantOrderSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        business = get_owner_business(self.request.user)
        queryset = _merchant_orders().filter(business=business)
        return _apply_merchant_filters(queryset, self.request.query_params)


class MerchantProductOrderListView(MerchantOrderListView):
    """GET /api/v1/orders/by-product/<product_id>/ — orders of one own product."""

    def get_queryset(self):
        business = get_owner_business(self.request.user)
        # all_objects: a soft-deleted product keeps its order history.
        owns_product = Product.all_objects.filter(
            pk=self.kwargs["product_id"], business=business
        ).exists()
        if not owns_product:
            raise NotFound("Product not found.")
        queryset = _merchant_orders().filter(
            business=business, product_id=self.kwargs["product_id"]
        )
        return _apply_merchant_filters(queryset, self.request.query_params)


class MerchantOrderDetailView(generics.RetrieveAPIView):
    """GET /api/v1/orders/<id>/"""

    serializer_class = MerchantOrderSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        business = get_owner_business(self.request.user)
        return _get_merchant_order(self.kwargs["pk"], business)


class MerchantOrderStatusView(APIView):
    """POST /api/v1/orders/<id>/status/  {"status": ..., "note": ...}"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        business = get_owner_business(request.user)
        serializer = OrderStatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_order_status(
            order_id=pk,
            business=business,
            actor=request.user,
            new_status=serializer.validated_data["status"],
            note=serializer.validated_data["note"],
        )
        return Response(MerchantOrderSerializer(_get_merchant_order(pk, business)).data)


class MerchantOrderNoteView(APIView):
    """POST /api/v1/orders/<id>/notes/  {"note": ..., "is_internal": bool}"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        business = get_owner_business(request.user)
        serializer = OrderNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = add_order_note(
            order_id=pk,
            business=business,
            actor=request.user,
            note=serializer.validated_data["note"],
            is_internal=serializer.validated_data["is_internal"],
        )
        return Response(
            MerchantOrderEventSerializer(event).data, status=status.HTTP_201_CREATED
        )


class MerchantOrderEventListView(APIView):
    """
    GET /api/v1/orders/<id>/events/ — every event (internal included),
    oldest first. Not paginated: an order has a handful of events.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        business = get_owner_business(request.user)
        order = _get_merchant_order(pk, business)
        events = order.events.all()
        return Response(MerchantOrderEventSerializer(events, many=True).data)


class CustomerOrderListView(generics.ListAPIView):
    """GET /api/v1/orders/my/ — the caller's own orders (filter: status)."""

    serializer_class = CustomerOrderSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        queryset = Order.objects.filter(customer=self.request.user).select_related(
            "business", "product"
        )
        status_value = _status_param(self.request.query_params)
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset


class CustomerOrderDetailView(generics.RetrieveAPIView):
    """GET /api/v1/orders/my/<id>/ — visible notes and events only."""

    serializer_class = CustomerOrderDetailSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        order = (
            Order.objects.select_related("business", "product")
            .filter(pk=self.kwargs["pk"], customer=self.request.user)
            .first()
        )
        if order is None:
            raise NotFound("Order not found.")
        return order
