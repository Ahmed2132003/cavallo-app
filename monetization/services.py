"""
Part P-086. Service layer for Featured subscriptions.

activate_subscription() is the ONLY sanctioned way a FeaturedSubscription
becomes active. The manual Django Admin path (this part) and the future
payment webhook (P-090) both call it identically; nothing else may set
the active flag to True directly.
"""

from django.db import transaction
from django.utils import timezone

from businesses.models import BusinessProfile
from monetization.models import FeaturedSubscription


def activate_subscription(business, plan):
    """
    Activate a Featured subscription of ``plan`` for ``business``.

    Inside a single transaction.atomic():
      1. Lock the business row, so two concurrent activations for the
         SAME business are serialised (the second waits for the first).
      2. Deactivate any currently-active subscription of that business.
         It is kept for history, never deleted: a new purchase
         supersedes the old one rather than stacking on top of it.
      3. Create and return the new active subscription. starts_at and
         expires_at are computed once by FeaturedSubscription.save().

    Payment-agnostic on purpose: no payment/webhook logic lives here.
    """
    with transaction.atomic():
        # all_objects so the lock also works for a soft-deleted business.
        BusinessProfile.all_objects.select_for_update().get(pk=business.pk)

        FeaturedSubscription.objects.filter(
            business_id=business.pk, is_active=True
        ).update(is_active=False, updated_at=timezone.now())

        return FeaturedSubscription.objects.create(
            business=business, plan=plan, is_active=True
        )