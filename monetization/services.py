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

from django.db import transaction
from django.utils import timezone

from businesses.models import BusinessProfile
from monetization.models import FeaturedSubscription


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