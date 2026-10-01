from datetime import timedelta

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import TimestampedModel
from products.models import Product


class Plan(TimestampedModel):
    """
    Part P-086. A fixed, admin-managed set of purchasable Featured
    tiers (e.g. 7 / 30 / 90 days).

    ``currency`` deliberately reuses ``Product.CURRENCY_CHOICES``
    (P-031) instead of defining a second, possibly-inconsistent list.
    """

    name = models.CharField(max_length=100)
    duration_days = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, choices=Product.CURRENCY_CHOICES)

    def __str__(self):
        return f"{self.name} ({self.duration_days}d, {self.price} {self.currency})"


class FeaturedSubscription(TimestampedModel):
    """
    Part P-086. One Featured purchase/grant for one business.

    ARCHITECTURE RULE: ``is_active`` is set to True ONLY by
    monetization.services.activate_subscription() (added in STEP 2).
    No webhook, Admin action or other code may set it directly.

    ``starts_at`` / ``expires_at`` are set ONCE, in save(), on first
    creation, from a single timezone.now() call (same pattern as
    stories.Story.published_at / expires_at, P-046). expires_at is
    computed and STORED, never derived live on read, and later saves
    never recompute it.
    """

    business = models.ForeignKey(
        "businesses.BusinessProfile",
        on_delete=models.CASCADE,
        related_name="featured_subscriptions",
    )
    # PROTECT: a Plan cannot be deleted while subscriptions reference it.
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="subscriptions",
    )
    starts_at = models.DateTimeField(editable=False)
    expires_at = models.DateTimeField(editable=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            # DB-level guarantee: never two simultaneously active
            # subscriptions for one business. (Name is <= 30 chars,
            # Django's models.E034 limit.)
            models.UniqueConstraint(
                fields=["business"],
                condition=Q(is_active=True),
                name="uniq_active_featured_per_biz",
            ),
        ]

    def __str__(self):
        return (
            f"FeaturedSubscription({self.pk}) business={self.business_id} "
            f"active={self.is_active}"
        )

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.starts_at is None:
                self.starts_at = timezone.now()
            if self.expires_at is None:
                self.expires_at = self.starts_at + timedelta(
                    days=self.plan.duration_days
                )
        super().save(*args, **kwargs)