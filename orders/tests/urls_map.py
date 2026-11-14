"""
Single place that names every endpoint, HTTP method and payload key the
BI-01 tests rely on.

The bundle does not contain the Part 2 urls.py / serializers, so the paths
below are the documented ASSUMPTION (REST-style, under /api/v1/orders/).
If your Part 2 routes differ, edit ONLY this file - no test body changes.
Set CUSTOMER_EVENTS = None to skip the customer events-endpoint tests when
events are embedded in the customer detail payload instead.
"""

FROM_CHAT = "/api/v1/orders/from-chat/"

MERCHANT_LIST = "/api/v1/orders/"
MERCHANT_DETAIL = "/api/v1/orders/{order_id}/"
MERCHANT_STATUS = "/api/v1/orders/{order_id}/status/"
MERCHANT_NOTES = "/api/v1/orders/{order_id}/notes/"
MERCHANT_EVENTS = "/api/v1/orders/{order_id}/events/"
PRODUCT_ORDERS = "/api/v1/orders/by-product/{product_id}/"

CUSTOMER_LIST = "/api/v1/orders/my/"
CUSTOMER_DETAIL = "/api/v1/orders/my/{order_id}/"
CUSTOMER_EVENTS = None

STATUS_METHOD = "post"
NOTES_METHOD = "post"

STATUS_FIELD = "status"
NOTE_FIELD = "note"
NOTE_INTERNAL_FIELD = "is_internal"


def url(template, **kwargs):
    return template.format(**kwargs)
