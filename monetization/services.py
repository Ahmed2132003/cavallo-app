"""
Parts P-086 / P-087. Service layer for Featured subscriptions.

activate_subscription() is the ONLY sanctioned way a FeaturedSubscription
becomes active. The manual Django Admin path (P-086) and the future
payment webhook (P-090) both call it identically; nothing else may set
the active flag to True directly.

Part P-087: BusinessProfile.is_featured is a REAL stored column (Feed's
`business__is_featured` ordering and Search's ordering/cursors depend on
it). It is a denormalised mirror of "this business has an active
FeaturedSubscription", written ONLY at the places where that state
legitimately changes, in the SAME transaction as the change:

  - activate_subscription()      -> is_featured = True
  - deactivate_subscriptions()   -> is_featured recomputed (False unless
                                    the business still has an active row)

The Part P-088 expiry job MUST call deactivate_subscriptions() rather
than flipping FeaturedSubscription.is_active itself, so the flag cannot
drift out of sync. Setting is_active=False directly elsewhere is
discouraged for the same reason.
"""

import logging
import math
import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from businesses.models import BusinessProfile
from monetization.constants import (
    BYTES_PER_GB,
    FREE_QUOTA_BYTES_DEFAULT,
    PURGE_GRACE_DAYS,
    TRIAL_DAYS_DEFAULT,
)
from monetization.models import FeaturedSubscription, Plan, Subscription

logger = logging.getLogger(__name__)


def _sync_business_featured_flag(business_ids):
    """
    Make BusinessProfile.is_featured match the subscription table for
    the given businesses: True if the business has an active
    FeaturedSubscription, False otherwise.

    Single source of truth is FeaturedSubscription.is_active; this only
    mirrors it. Uses queryset .update() (no save(), no post_save
    signals) so unrelated handlers - e.g. the search-vector signal - do
    not re-run for a flag flip. Callers must already be inside
    transaction.atomic().
    """
    business_ids = set(business_ids)
    if not business_ids:
        return

    featured_ids = set(
        FeaturedSubscription.objects.filter(
            business_id__in=business_ids, is_active=True
        ).values_list("business_id", flat=True)
    )
    now = timezone.now()
    # all_objects so a soft-deleted business is kept consistent too.
    BusinessProfile.all_objects.filter(pk__in=featured_ids).update(
        is_featured=True, updated_at=now
    )
    BusinessProfile.all_objects.filter(pk__in=business_ids - featured_ids).update(
        is_featured=False, updated_at=now
    )

    # Part P-094 (finding F-9): the public Business Profile response is
    # cached for 5 minutes (P-030) and exposes is_featured, but the
    # .update() calls above fire no signals, so the cached copy kept the
    # old flag. Drop it once the surrounding transaction has COMMITTED
    # (a concurrent reader could otherwise re-cache the old value between
    # the delete and the commit). Same pattern as F-2's User signal.
    def _drop_cached_profiles():
        # Imported lazily: businesses.views imports a lot at module load.
        from businesses.views import _business_profile_cache_key

        cache.delete_many([_business_profile_cache_key(pk) for pk in business_ids])

    transaction.on_commit(_drop_cached_profiles)


def activate_subscription(business, plan):
    """
    Activate a Featured subscription of ``plan`` for ``business``.

    Inside a single transaction.atomic():
      1. Lock the business row, so two concurrent activations for the
         SAME business are serialised (the second waits for the first).
      2. Deactivate any currently-active subscription of that business.
         It is kept for history, never deleted: a new purchase
         supersedes the old one rather than stacking on top of it.
      3. Create the new active subscription. starts_at and expires_at
         are computed once by FeaturedSubscription.save().
      4. (P-087) Set business.is_featured = True, in the same
         transaction. If anything above fails, the flag is untouched.

    The passed-in ``business`` instance is updated in memory too, so
    callers see is_featured=True without a refresh_from_db().

    Payment-agnostic on purpose: no payment/webhook logic lives here.
    """
    with transaction.atomic():
        # all_objects so the lock also works for a soft-deleted business.
        BusinessProfile.all_objects.select_for_update().get(pk=business.pk)

        FeaturedSubscription.objects.filter(
            business_id=business.pk, is_active=True
        ).update(is_active=False, updated_at=timezone.now())

        subscription = FeaturedSubscription.objects.create(
            business=business, plan=plan, is_active=True
        )

        _sync_business_featured_flag([business.pk])
        business.is_featured = True
        return subscription


def deactivate_subscriptions(subscriptions):
    """
    Deactivate every ACTIVE subscription in ``subscriptions`` (a
    FeaturedSubscription queryset) and un-feature the affected
    businesses, atomically. Returns the number of subscriptions that
    were actually deactivated.

    Rows are kept for history, never deleted. Already-inactive rows in
    the queryset are ignored, so a business is only un-featured when one
    of ITS active rows is being deactivated.

    Used by the Admin "Deactivate selected" action now, and by the
    Part P-088 expiry job (pass the queryset of expired active rows).
    """
    with transaction.atomic():
        rows = list(
            subscriptions.filter(is_active=True).values_list("pk", "business_id")
        )
        if not rows:
            return 0

        subscription_ids = [pk for pk, _ in rows]
        business_ids = sorted({business_id for _, business_id in rows})

        # Same lock activate_subscription() takes, in a stable order
        # (pk ASC) so concurrent calls cannot deadlock each other.
        list(
            BusinessProfile.all_objects.select_for_update()
            .filter(pk__in=business_ids)
            .order_by("pk")
            .values_list("pk", flat=True)
        )

        updated = FeaturedSubscription.objects.filter(
            pk__in=subscription_ids, is_active=True
        ).update(is_active=False, updated_at=timezone.now())

        _sync_business_featured_flag(business_ids)
        return updated


# ---------------------------------------------------------------------
# BE-02: storage-plan subscription state (read-only logic, no enforcement)
# ---------------------------------------------------------------------


def create_free_subscription(business, now=None):
    """
    Create the Free trial subscription for ``business`` if it has none.
    Idempotent: returns the existing row when there is one. Returns None
    (and logs a warning) if no Plan with code="free" exists.
    """
    existing = Subscription.objects.filter(business_id=business.pk).first()
    if existing is not None:
        return existing

    plan = Plan.objects.filter(code="free").first()
    if plan is None:
        logger.warning("No 'free' Plan; skipping subscription for %s", business.pk)
        return None

    now = now or timezone.now()
    trial_ends_at = now + timedelta(days=plan.trial_days or TRIAL_DAYS_DEFAULT)
    return Subscription.objects.create(
        business_id=business.pk,
        plan=plan,
        status=Subscription.STATUS_TRIALING,
        started_at=now,
        trial_ends_at=trial_ends_at,
        purge_at=trial_ends_at + timedelta(days=PURGE_GRACE_DAYS),
        quota_bytes=(
            plan.storage_bytes
            if plan.storage_bytes is not None
            else FREE_QUOTA_BYTES_DEFAULT
        ),
    )


def get_subscription(business):
    """The business's current Subscription (plan preloaded) or None."""
    return (
        Subscription.objects.select_related("plan")
        .filter(business_id=business.pk)
        .first()
    )


def _state(state, days_left, locked, sub):
    return {
        "state": state,
        "days_left": days_left,
        "locked": locked,
        "purge_at": sub.purge_at if sub is not None else None,
    }


def effective_state(business, now=None):
    """
    Subscription state computed from the DATES, not only the stored
    ``status``. Returns {state, days_left, locked, purge_at}.

    state: "trialing" | "active" | "expired" | "locked" | "none".
      - stored "locked"/"expired"  -> locked, regardless of dates.
      - trialing: ends at trial_ends_at; active: ends at
        current_period_end. now >= end  -> "expired", locked.
      - an unset end date means open-ended (never expires by date).
      - no subscription row -> "none", not locked (customers,
        moderators and legacy rows are never locked).
    days_left: whole days remaining, rounded up; 0 once ended; None when
    open-ended.
    """
    now = now or timezone.now()
    sub = get_subscription(business)
    if sub is None:
        return _state("none", 0, False, None)
    if sub.status == Subscription.STATUS_LOCKED:
        return _state("locked", 0, True, sub)
    if sub.status == Subscription.STATUS_EXPIRED:
        return _state("expired", 0, True, sub)

    end = (
        sub.trial_ends_at
        if sub.status == Subscription.STATUS_TRIALING
        else sub.current_period_end
    )
    if end is None:
        return _state(sub.status, None, False, sub)
    if now >= end:
        return _state("expired", 0, True, sub)
    days_left = math.ceil((end - now).total_seconds() / 86400)
    return _state(sub.status, days_left, False, sub)


def is_locked(business, now=None):
    """True when the business's subscription has ended or is locked."""
    return effective_state(business, now=now)["locked"]


# ---------------------------------------------------------------------
# BE-03: Infinity (custom storage plan) pricing. Pure functions, no DB.
# ---------------------------------------------------------------------

# Plain decimal only: no sign, no exponent, at most 2 decimal places.
_GB_RE = re.compile(r"\d{1,6}(?:\.\d{1,2})?")
_TWO_PLACES = Decimal("0.01")


def _custom_pricing_config():
    """(min_gb, max_gb, price_per_gb, min_price) as Decimals, read at call time."""
    return (
        Decimal(str(settings.CUSTOM_MIN_GB)),
        Decimal(str(settings.CUSTOM_MAX_GB)),
        Decimal(str(settings.CUSTOM_PRICE_PER_GB)),
        Decimal(str(settings.CUSTOM_MIN_PRICE)),
    )


def _fmt(value):
    return format(value.normalize(), "f")


def parse_gb(raw):
    """
    Turn user input (str / int / float / Decimal) into a Decimal GB value
    WITHOUT going through binary float math: the value is converted via
    its text form. Rejects bool, None, signs, exponents ("1e2"), NaN /
    Infinity and more than two decimal places. Raises DRF ValidationError
    keyed on "gb" (-> 400 in the project's error envelope).
    """
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
        raise ValidationError({"gb": "A numeric gb value is required."})
    text = str(raw).strip()
    if not _GB_RE.fullmatch(text):
        raise ValidationError(
            {"gb": "gb must be a plain number with at most two decimal places."}
        )
    return Decimal(text)


def calculate_custom_price(gb):
    """
    Price in EGP (Decimal, 2 places) of a custom Infinity plan of ``gb`` GB:
    ceil(gb * CUSTOM_PRICE_PER_GB), then at least CUSTOM_MIN_PRICE.

    ``gb`` may be a Decimal, str, int or float (max two decimals). Raises
    ValidationError when gb is invalid or outside
    [CUSTOM_MIN_GB, CUSTOM_MAX_GB].
    """
    gb = parse_gb(gb)
    min_gb, max_gb, price_per_gb, min_price = _custom_pricing_config()
    if gb < min_gb:
        raise ValidationError({"gb": f"gb must be at least {_fmt(min_gb)}."})
    if gb > max_gb:
        raise ValidationError({"gb": f"gb must be at most {_fmt(max_gb)}."})

    price = max(Decimal(math.ceil(gb * price_per_gb)), min_price)
    return price.quantize(_TWO_PLACES)


def custom_quota_bytes(gb):
    """
    Bytes for ``gb`` GB, using BYTES_PER_GB (1024**3, same as the BE-01
    catalog seed). Fractions of a byte are dropped (floor): never promise
    more storage than was paid for. Does NOT range-check; call
    calculate_custom_price() for validation.
    """
    return int(parse_gb(gb) * BYTES_PER_GB)
