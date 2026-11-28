"""
Part BI-02 - shared constants for tracking, rollups and the dashboard APIs.

The order status literals mirror orders.constants.OrderStatus (that file is
not part of the review bundle). analytics/test_bi02_dashboard.py asserts
they all exist in OrderStatus.values, so a rename there fails a test here.
Sales Value (BI-01) = confirmed + processing + shipped + delivered.
"""

TARGET_PRODUCT = "product"
TARGET_POST = "post"
TARGET_REEL = "reel"
TARGET_TYPES = (TARGET_PRODUCT, TARGET_POST, TARGET_REEL)
CONTENT_KINDS = (TARGET_POST, TARGET_REEL)

STATUS_PENDING = "pending"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "delivered"
SALES_STATUSES = ("confirmed", "processing", "shipped", "delivered")
ALL_ORDER_STATUSES = (
    "pending",
    "confirmed",
    "processing",
    "shipped",
    "delivered",
    "cancelled",
)

PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}
DEFAULT_PERIOD = "30d"
MAX_RANGE_DAYS = 366

MAX_WATCH_MS = 86_400_000
DEFAULT_REEL_MIN_WATCH_MS = 3000
EVENT_MAX_AGE_HOURS = 48
EVENT_FUTURE_TOLERANCE_MINUTES = 5

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
