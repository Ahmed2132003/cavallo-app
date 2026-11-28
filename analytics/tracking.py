"""
Part BI-02 - view tracking service.

Rules enforced here (the HTTP view is a thin wrapper):
  - the target must exist AND be active: Product -> is_active and not
    soft-deleted; Post/Reel -> published_objects (published, not deleted,
    reel also processing_status=ready); its business must not be soft-deleted.
  - the owner of the item never counts as a viewer (403).
  - a reel view needs watch_ms >= settings.ANALYTICS_REEL_MIN_WATCH_MS,
    otherwise it is ignored (200, nothing stored, the client may resend later
    with a longer watch time).
  - event_id is unique: a replay returns 200 "duplicate" and stores nothing.
  - viewer_key is an HMAC-SHA256 hex digest; no IP is ever stored.

The client must call the endpoint when the DETAIL screen opens or the video
starts playing, never when the item merely appears in a list. The server
cannot see that difference, so list endpoints never record anything.
"""

import hashlib
import hmac
from datetime import timezone as dt_timezone

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from analytics import constants as C
from analytics.models import ContentViewEvent, ProductViewEvent
from businesses.models import BusinessProfile
from content.models import Post, Reel
from products.models import Product


def _salt():
    return (
        getattr(settings, "ANALYTICS_VIEWER_KEY_SALT", "") or settings.SECRET_KEY
    ).encode("utf-8")


def build_viewer_key(*, user, anon_id):
    """Logged in -> HMAC of the user id; otherwise HMAC of the install id."""
    if user is not None and getattr(user, "is_authenticated", False):
        raw = f"user:{user.pk}"
    else:
        raw = f"anon:{anon_id}"
    return hmac.new(_salt(), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def reel_min_watch_ms():
    return int(
        getattr(settings, "ANALYTICS_REEL_MIN_WATCH_MS", C.DEFAULT_REEL_MIN_WATCH_MS)
    )


def resolve_target(target_type, target_id):
    """Return (object, business) or raise NotFound."""
    if target_type == C.TARGET_PRODUCT:
        obj = Product.objects.filter(pk=target_id, is_active=True).first()
    elif target_type == C.TARGET_POST:
        obj = Post.published_objects.filter(pk=target_id).first()
    else:
        obj = Reel.published_objects.filter(pk=target_id).first()
    if obj is None:
        raise NotFound("Target not found.")
    business = BusinessProfile.objects.filter(pk=obj.business_id).first()
    if business is None:
        raise NotFound("Target not found.")
    return obj, business


def record_view_event(*, user, data):
    """Return (http_status, body)."""
    kind = data["target_type"]
    obj, business = resolve_target(kind, data["target_id"])

    if user.is_authenticated and business.user_id == user.id:
        raise PermissionDenied("Views by the owner of an item are not recorded.")

    watch_ms = data.get("watch_ms")
    if kind == C.TARGET_REEL:
        minimum = reel_min_watch_ms()
        if watch_ms is None or watch_ms < minimum:
            return 200, {
                "status": "ignored",
                "reason": "below_min_watch_ms",
                "min_watch_ms": minimum,
            }

    occurred_at = (data.get("occurred_at") or timezone.now()).astimezone(
        dt_timezone.utc
    )
    common = {
        "viewer_key": build_viewer_key(user=user, anon_id=data.get("anon_id")),
        "business": business,
        "occurred_at": occurred_at,
        "view_date": occurred_at.date(),
    }
    event_id = data["event_id"]

    if kind == C.TARGET_PRODUCT:
        _, created = ProductViewEvent.objects.get_or_create(
            event_id=event_id, defaults={**common, "product": obj}
        )
    else:
        _, created = ContentViewEvent.objects.get_or_create(
            event_id=event_id,
            defaults={
                **common,
                "content_kind": kind,
                "object_id": obj.pk,
                "watch_ms": watch_ms if kind == C.TARGET_REEL else None,
            },
        )

    if created:
        return 201, {"status": "recorded", "event_id": str(event_id)}
    return 200, {"status": "duplicate", "event_id": str(event_id)}
