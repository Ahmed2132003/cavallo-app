"""
Part P-064 STEP 1 — search app cursor: shape, encoding and decoding.

Why a custom cursor (mirrors feed/cursor.py's own reasoning, P-059)
--------------------------------------------------------------------
core.pagination.StandardCursorPagination orders ONE queryset by ONE
field. This endpoint merges two tables (BusinessProfile + Product) and
sorts by (is_featured DESC, relevance-or-recency DESC) per this part's
own execution prompt, so — exactly like feed/cursor.py before it — it
needs a cursor that carries a composite position across both tables.
Still strictly cursor-based (never offset/limit), per architecture
Section 9 point 7.

What "relevance-or-recency" means here
---------------------------------------
Every single request picks exactly ONE secondary ordering key, for its
entire lifetime (including every later page a client asks for with
this cursor):

    mode == "relevance"  ->  secondary key is Postgres's SearchRank
                              (a float), used only when ?q= is present
    mode == "recency"    ->  secondary key is created_at (a datetime),
                              used when ?q= is absent/empty

A cursor issued in one mode can never be resumed in the other — if a
client's stored cursor's mode does not match the mode the CURRENT
request would use (i.e. whether ?q= is present now), decode_cursor()
raises InvalidCursorError. This is deliberate: mixing a stored
SearchRank value against a different (or absent) full-text query would
silently produce nonsense pagination, not a subtle bug to paper over.

What a cursor means
--------------------
A cursor is the position of the LAST item of the previous page:

    mode         "relevance" | "recency" — see above
    is_featured  whether that item's owning business is Featured
    secondary    the item's rank (relevance) or created_at (recency)
    content_type "business" | "product"
    object_id    the item's primary key

The next page resumes strictly AFTER that position, in the current
request's (is_featured DESC, secondary DESC) total order.

Wire format (Flutter treats it as an opaque string — same convention
as feed/cursor.py / P-061)
------------------------------------------------------------------
    base64url( compact JSON ) with the "=" padding removed

JSON keys (all required, no others allowed):
    relevance mode: {"v":1,"m":"r","ft":0|1,"sec":<float>,"t":"business"|"product","id":<int>}
    recency mode  : {"v":1,"m":"c","ft":0|1,"sec":"<ISO-8601 UTC>","t":"business"|"product","id":<int>}

Anything that does not match this exact shape, or whose mode does not
match `expected_mode`, raises InvalidCursorError. The message is
deliberately generic so a caller learns nothing about why a forged
cursor failed — same convention as feed/cursor.py.
"""

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Union

CURSOR_VERSION = 1
MAX_CURSOR_LENGTH = 512

MODE_RELEVANCE = "relevance"
MODE_RECENCY = "recency"
MODES = (MODE_RELEVANCE, MODE_RECENCY)
_MODE_WIRE = {MODE_RELEVANCE: "r", MODE_RECENCY: "c"}
_WIRE_MODE = {v: k for k, v in _MODE_WIRE.items()}

CONTENT_TYPE_BUSINESS = "business"
CONTENT_TYPE_PRODUCT = "product"
CONTENT_TYPES = (CONTENT_TYPE_BUSINESS, CONTENT_TYPE_PRODUCT)
# Tie-break between a Business and a Product that share the exact same
# (is_featured, secondary) key: lower rank sorts first in the
# comparison below. Arbitrary but fixed, same role as
# feed/cursor.py's own CONTENT_TYPE_RANK.
CONTENT_TYPE_RANK = {CONTENT_TYPE_BUSINESS: 0, CONTENT_TYPE_PRODUCT: 1}

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_KEYS = frozenset({"v", "m", "ft", "sec", "t", "id"})


class InvalidCursorError(ValueError):
    """The cursor string is malformed, forged, from another version, or
    was issued in a mode that does not match the current request."""


@dataclass(frozen=True)
class SearchCursor:
    mode: str
    is_featured: bool
    secondary: Union[float, datetime]
    content_type: str
    object_id: int

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"Unknown search cursor mode: {self.mode!r}")
        if self.content_type not in CONTENT_TYPES:
            raise ValueError(f"Unknown content type: {self.content_type!r}")
        if type(self.object_id) is not int or self.object_id <= 0:
            raise ValueError("object_id must be a positive integer")
        if type(self.is_featured) is not bool:
            raise ValueError("is_featured must be a bool")
        if self.mode == MODE_RELEVANCE:
            if isinstance(self.secondary, bool) or not isinstance(
                self.secondary, (int, float)
            ):
                raise ValueError("A relevance cursor's secondary key must be a number")
            object.__setattr__(self, "secondary", float(self.secondary))
        else:
            if not isinstance(self.secondary, datetime) or self.secondary.tzinfo is None:
                raise ValueError(
                    "A recency cursor's secondary key must be a timezone-aware datetime"
                )
            object.__setattr__(
                self, "secondary", self.secondary.astimezone(timezone.utc)
            )


def encode_cursor(cursor: SearchCursor) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "m": _MODE_WIRE[cursor.mode],
        "ft": 1 if cursor.is_featured else 0,
        "sec": (
            cursor.secondary
            if cursor.mode == MODE_RELEVANCE
            else cursor.secondary.isoformat()
        ),
        "t": cursor.content_type,
        "id": cursor.object_id,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(token: str, *, expected_mode: str) -> SearchCursor:
    if expected_mode not in MODES:
        raise ValueError(f"Unknown expected mode: {expected_mode!r}")
    if (
        not isinstance(token, str)
        or not token
        or len(token) > MAX_CURSOR_LENGTH
        or not _TOKEN_PATTERN.match(token)
    ):
        raise InvalidCursorError("Invalid search cursor.")

    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except ValueError:  # bad base64, bad UTF-8 and bad JSON are all ValueErrors
        raise InvalidCursorError("Invalid search cursor.") from None

    if not isinstance(payload, dict) or set(payload) != _KEYS:
        raise InvalidCursorError("Invalid search cursor.")
    if type(payload.get("v")) is not int or payload["v"] != CURSOR_VERSION:
        raise InvalidCursorError("Invalid search cursor.")

    wire_mode = payload.get("m")
    if wire_mode not in _WIRE_MODE:
        raise InvalidCursorError("Invalid search cursor.")
    mode = _WIRE_MODE[wire_mode]
    if mode != expected_mode:
        # The cursor was issued for a different mode (e.g. the client
        # now sends a different/absent ?q= than when this cursor was
        # handed out). Never silently resume across modes.
        raise InvalidCursorError("Invalid search cursor.")

    ft = payload["ft"]
    if type(ft) is not int or ft not in (0, 1):
        raise InvalidCursorError("Invalid search cursor.")

    content_type = payload["t"]
    if content_type not in CONTENT_TYPES:
        raise InvalidCursorError("Invalid search cursor.")

    object_id = payload["id"]
    if type(object_id) is not int:
        raise InvalidCursorError("Invalid search cursor.")

    sec = payload["sec"]
    if mode == MODE_RELEVANCE:
        if isinstance(sec, bool) or not isinstance(sec, (int, float)):
            raise InvalidCursorError("Invalid search cursor.")
    else:
        if not isinstance(sec, str):
            raise InvalidCursorError("Invalid search cursor.")

    try:
        secondary = float(sec) if mode == MODE_RELEVANCE else datetime.fromisoformat(sec)
        return SearchCursor(
            mode=mode,
            is_featured=bool(ft),
            secondary=secondary,
            content_type=content_type,
            object_id=object_id,
        )
    except ValueError:
        raise InvalidCursorError("Invalid search cursor.") from None