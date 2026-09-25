"""
Part P-059 — Home Feed cursor: shape, encoding and decoding.

Why a custom cursor (and not core.pagination.StandardCursorPagination)
----------------------------------------------------------------------
StandardCursorPagination orders ONE queryset by ONE field. The Home
Feed merges two tables (Post + Reel) and moves through two tiers
(followed businesses first, then a Featured-first backfill), so it needs
a cursor that remembers the tier and a composite position. It is still
cursor-based (never offset/limit), which is what architecture Section 9
point 7 actually requires.

What the cursor means
---------------------
A cursor is the position of the LAST item of the previous page:

    phase        "f" -> that item came from the FOLLOWING tier
                 "b" -> that item came from the BACKFILL tier
    created_at   the item's created_at (timezone-aware, normalised to UTC)
    content_type "post" or "reel"
    object_id    the item's primary key
    is_featured  BACKFILL only: whether the item's business is Featured

The next page resumes strictly AFTER that position, in that tier's
ordering. A cursor never says "I was backfilling in the middle of a
page": the phase is simply the tier of the last item served, which is
all that is needed to resume (the full reasoning lives in
feed/services.py).

Wire format (Flutter treats it as an opaque string, P-061)
----------------------------------------------------------
    base64url( compact JSON ) with the "=" padding removed

JSON keys (all required, no others allowed):

    following: {"v":1,"p":"f","ts":"<ISO-8601 UTC>","t":"post|reel","id":<int>}
    backfill : {"v":1,"p":"b","ts":"<ISO-8601 UTC>","t":"post|reel","id":<int>,
                "ft":0|1}

Example (following): the JSON
    {"id":12,"p":"f","t":"post","ts":"2026-09-24T09:41:09.887123+00:00","v":1}
encodes to a single URL-safe string. Clients must send it back verbatim
as ?cursor=<string>; they must never parse or build one.

Anything that does not match this exact shape raises InvalidCursorError.
The message is deliberately generic so a caller learns nothing about
why a forged cursor failed.
"""

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

CURSOR_VERSION = 1
MAX_CURSOR_LENGTH = 512

PHASE_FOLLOWING = "f"
PHASE_BACKFILL = "b"
PHASES = (PHASE_FOLLOWING, PHASE_BACKFILL)

CONTENT_TYPE_POST = "post"
CONTENT_TYPE_REEL = "reel"
CONTENT_TYPES = (CONTENT_TYPE_POST, CONTENT_TYPE_REEL)
# Tie-break between a Post and a Reel that share the exact same
# created_at: higher rank sorts first in the (descending) feed order,
# so at an identical timestamp a Reel comes before a Post. Any fixed
# rule works; what matters is that it never changes.
CONTENT_TYPE_RANK = {CONTENT_TYPE_POST: 0, CONTENT_TYPE_REEL: 1}

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_FOLLOWING_KEYS = frozenset({"v", "p", "ts", "t", "id"})
_BACKFILL_KEYS = _FOLLOWING_KEYS | {"ft"}


class InvalidCursorError(ValueError):
    """The cursor string is malformed, forged, or from another version."""


@dataclass(frozen=True)
class FeedCursor:
    phase: str
    created_at: datetime
    content_type: str
    object_id: int
    is_featured: Optional[bool] = None

    def __post_init__(self):
        if self.phase not in PHASES:
            raise ValueError(f"Unknown feed phase: {self.phase!r}")
        if self.content_type not in CONTENT_TYPES:
            raise ValueError(f"Unknown content type: {self.content_type!r}")
        if type(self.object_id) is not int or self.object_id <= 0:
            raise ValueError("object_id must be a positive integer")
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise ValueError("created_at must be a timezone-aware datetime")
        if self.phase == PHASE_BACKFILL:
            if type(self.is_featured) is not bool:
                raise ValueError("A backfill cursor needs is_featured (bool)")
        elif self.is_featured is not None:
            raise ValueError("A following cursor must not carry is_featured")
        # Normalise so the same instant always encodes to the same string.
        object.__setattr__(self, "created_at", self.created_at.astimezone(timezone.utc))


def encode_cursor(cursor: FeedCursor) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "p": cursor.phase,
        "ts": cursor.created_at.isoformat(),
        "t": cursor.content_type,
        "id": cursor.object_id,
    }
    if cursor.phase == PHASE_BACKFILL:
        payload["ft"] = 1 if cursor.is_featured else 0
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(token: str) -> FeedCursor:
    if (
        not isinstance(token, str)
        or not token
        or len(token) > MAX_CURSOR_LENGTH
        or not _TOKEN_PATTERN.match(token)
    ):
        raise InvalidCursorError("Invalid feed cursor.")

    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except ValueError:  # bad base64, bad UTF-8 and bad JSON are all ValueErrors
        raise InvalidCursorError("Invalid feed cursor.") from None

    if not isinstance(payload, dict) or type(payload.get("v")) is not int:
        raise InvalidCursorError("Invalid feed cursor.")
    if payload["v"] != CURSOR_VERSION:
        raise InvalidCursorError("Invalid feed cursor.")

    phase = payload.get("p")
    if not isinstance(phase, str) or phase not in PHASES:
        raise InvalidCursorError("Invalid feed cursor.")
    expected_keys = _FOLLOWING_KEYS if phase == PHASE_FOLLOWING else _BACKFILL_KEYS
    if set(payload) != expected_keys:
        raise InvalidCursorError("Invalid feed cursor.")

    ts = payload["ts"]
    if not isinstance(ts, str):
        raise InvalidCursorError("Invalid feed cursor.")

    is_featured = None
    if phase == PHASE_BACKFILL:
        ft = payload["ft"]
        if type(ft) is not int or ft not in (0, 1):
            raise InvalidCursorError("Invalid feed cursor.")
        is_featured = bool(ft)

    try:
        return FeedCursor(
            phase=phase,
            created_at=datetime.fromisoformat(ts),
            content_type=payload["t"],
            object_id=payload["id"],
            is_featured=is_featured,
        )
    except ValueError:
        raise InvalidCursorError("Invalid feed cursor.") from None
