"""
BE-06. SQL mirror of services.effective_state()'s "locked" rule, so public
queryset can hide a locked merchant's content in the DB.

KEEP IN SYNC with effective_state(): locked when stored status is
locked/expired, or trialing past trial_ends_at, or active past
current_period_end. NULL end date = open-ended. No Subscription row
(customers, legacy) = never locked.
"""

from django.db.models import Q
from django.utils import timezone

from monetization.models import Subscription


def locked_businesses_q(prefix="business", now=None):
    """Q matching rows whose business (reached via ``prefix``) is locked.
    Use prefix="" when filtering BusinessProfile itself."""
    now = now or timezone.now()
    p = f"{prefix}__subscription__" if prefix else "subscription__"
    return (
        Q(**{f"{p}status__in": [Subscription.STATUS_LOCKED, Subscription.STATUS_EXPIRED]})
        | Q(**{f"{p}status": Subscription.STATUS_TRIALING, f"{p}trial_ends_at__lte": now})
        | Q(**{f"{p}status": Subscription.STATUS_ACTIVE, f"{p}current_period_end__lte": now})
    )


def lock_state(business_id, now=None):
    """
    One query. Returns (locked, end) where ``end`` is the datetime at which
    an unlocked subscription will lock (None = open-ended / no row / locked).
    Same rule as services.effective_state().
    """
    now = now or timezone.now()
    row = (
        Subscription.objects.filter(business_id=business_id)
        .values_list("status", "trial_ends_at", "current_period_end")
        .first()
    )
    if row is None:
        return False, None
    status, trial_end, period_end = row
    if status in (Subscription.STATUS_LOCKED, Subscription.STATUS_EXPIRED):
        return True, None
    end = trial_end if status == Subscription.STATUS_TRIALING else period_end
    if end is not None and now >= end:
        return True, None
    return False, end


def exclude_locked_businesses(queryset, prefix="business", now=None):
    """Hide content of locked merchants. Add AFTER the published/moderation filters."""
    return queryset.exclude(locked_businesses_q(prefix, now))