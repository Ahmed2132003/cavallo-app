"""
BE-10: the EFFECTIVE Featured state of a business.

BusinessProfile.is_featured
    Manual / Admin Featured: the P-087 mirror of an active
    FeaturedSubscription. NOT touched here; still written only by
    monetization.services._sync_business_featured_flag().

BusinessProfile.is_featured_effective
    What Feed / Discover / Search ranking and the public business payload
    read:  is_featured  OR  the storage plan grants Featured.

A storage plan grants Featured only when its code is in
FEATURED_PLAN_CODES (ultra, infinity) AND the Subscription status is
trialing/active. Expired/locked rows grant nothing (and a locked business
is hidden from public reads anyway, BE-06). The rule is code-based on
purpose: a misconfigured Plan.is_featured on pro/max never features.

Written ONLY by sync_featured_effective(), via queryset .update() (no
save(), so no post_save loops and no search-vector re-run). Callers:
  - monetization.services._sync_business_featured_flag (manual changes)
  - businesses.signals (Subscription save/delete, BusinessProfile save),
    which covers activation, upgrade, pending downgrade, expiry and
    deletion without touching the lifecycle / payment code.
"""

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from businesses.models import BusinessProfile
from monetization.models import Subscription

FEATURED_PLAN_CODES = frozenset({"ultra", "infinity"})
_RUNNING_STATUSES = (Subscription.STATUS_TRIALING, Subscription.STATUS_ACTIVE)


def plan_grants_featured(plan) -> bool:
    """True when ``plan`` is one of the storage plans that grant Featured."""
    return plan is not None and plan.code in FEATURED_PLAN_CODES


def featured_effective_ids(business_ids) -> set:
    """The subset of ``business_ids`` whose effective Featured is True."""
    ids = set(business_ids)
    if not ids:
        return set()
    manual = set(
        BusinessProfile.all_objects.filter(pk__in=ids, is_featured=True).values_list(
            "pk", flat=True
        )
    )
    by_plan = set(
        Subscription.objects.filter(
            business_id__in=ids,
            status__in=_RUNNING_STATUSES,
            plan__code__in=FEATURED_PLAN_CODES,
        ).values_list("business_id", flat=True)
    )
    return manual | by_plan


def sync_featured_effective(business_ids, *, drop_cache=True) -> int:
    """
    Recompute BusinessProfile.is_featured_effective for ``business_ids``.
    Idempotent; only rows whose value changes are written. Returns the
    number of rows changed. The cached public profile is dropped after
    commit when something changed (unless the caller already does it).
    """
    ids = {pk for pk in business_ids if pk is not None}
    if not ids:
        return 0
    on_ids = featured_effective_ids(ids)
    now = timezone.now()
    changed = BusinessProfile.all_objects.filter(
        pk__in=on_ids, is_featured_effective=False
    ).update(is_featured_effective=True, updated_at=now)
    changed += BusinessProfile.all_objects.filter(
        pk__in=ids - on_ids, is_featured_effective=True
    ).update(is_featured_effective=False, updated_at=now)

    if changed and drop_cache:

        def _drop():
            from businesses.views import _business_profile_cache_key

            cache.delete_many([_business_profile_cache_key(pk) for pk in ids])

        transaction.on_commit(_drop)
    return changed