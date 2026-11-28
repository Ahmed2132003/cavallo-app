from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from decimal import ROUND_HALF_UP, Decimal


def day_bounds(date_from, date_to):
    """
    [start, end) UTC datetimes covering date_from..date_to inclusive.
    Used instead of ``created_at__date`` so composite indexes on the
    datetime column stay usable.
    """
    start = datetime.combine(date_from, time.min, tzinfo=dt_timezone.utc)
    end = datetime.combine(
        date_to + timedelta(days=1), time.min, tzinfo=dt_timezone.utc
    )
    return start, end


def money(value):
    """Decimal -> '1234.50' (string, 2 places); None stays None."""
    if value is None:
        return None
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def ratio(numerator, denominator):
    """
    numerator / denominator rounded to 4 places, or None when either side
    is missing or the denominator is 0 (never 0, never infinity).
    """
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator, 4)


def iso(value):
    """aware datetime -> ISO-8601 with a trailing Z; None stays None."""
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")
