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

    # ---- BE-01: subscription catalog fields -------------------------
    # Legacy Featured plans keep code=NULL (unique allows many NULLs).
    CODE_CHOICES = [
        ("free", "Free"),
        ("pro", "Pro"),
        ("max", "Max"),
        ("ultra", "Ultra"),
        ("infinity", "Infinity"),
    ]
    code = models.CharField(
        max_length=20, unique=True, null=True, blank=True, choices=CODE_CHOICES
    )
    name_ar = models.CharField(max_length=100, blank=True, default="")
    name_en = models.CharField(max_length=100, blank=True, default="")
    # NULL = not fixed in advance (infinity / is_custom).
    price_egp = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    storage_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    is_featured = models.BooleanField(default=False)
    trial_days = models.PositiveIntegerField(default=0)
    is_custom = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(trial_days=0) | Q(code="free"),
                name="plan_trial_days_only_free",
            ),
        ]

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


class Subscription(TimestampedModel):
    """
    BE-02. The CURRENT storage-plan subscription of one business
    (exactly one row per BusinessProfile). Separate from
    FeaturedSubscription, which keeps per-purchase Featured history.

    ``status`` is the STORED value; whether a business is actually
    locked is computed from the dates by
    monetization.services.effective_state() / is_locked().
    This model enforces nothing by itself (no locking, no purge).
    """

    STATUS_TRIALING = "trialing"
    STATUS_ACTIVE = "active"
    STATUS_EXPIRED = "expired"
    STATUS_LOCKED = "locked"
    STATUS_CHOICES = [
        (STATUS_TRIALING, "Trialing"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_LOCKED, "Locked"),
    ]

    business = models.OneToOneField(
        "businesses.BusinessProfile",
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    # related_name differs from FeaturedSubscription.plan ("subscriptions").
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="storage_subscriptions",
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_TRIALING
    )
    started_at = models.DateTimeField()
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    # The quota actually applied (custom plans differ from plan.storage_bytes).
    quota_bytes = models.PositiveBigIntegerField()
    custom_gb = models.PositiveIntegerField(null=True, blank=True)
    is_featured_effective = models.BooleanField(default=False)
    purge_at = models.DateTimeField(null=True, blank=True)
    pending_plan = models.ForeignKey(
        Plan,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # BE-08: quota paid for the pending (downgrade) plan.
    pending_quota_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return (
            f"Subscription({self.pk}) business={self.business_id} "
            f"plan={self.plan_id} status={self.status}"
        )


class StoredFile(TimestampedModel):
    """
    BE-04. Ledger of the files that count toward a business's storage
    usage: ONE row per (model, object_id, field) slot, holding the size
    captured at upload time (no per-read S3 HEAD calls).

    Written ONLY by monetization.storage_usage / storage_signals.
    Used bytes = SUM(size_bytes) of a business's rows. Chat media and
    avatars are deliberately never recorded here (D10).
    """

    business = models.ForeignKey(
        "businesses.BusinessProfile",
        on_delete=models.CASCADE,
        related_name="stored_files",
    )
    # Model label, e.g. "content.Reel".
    model = models.CharField(max_length=60)
    object_id = models.PositiveBigIntegerField()
    field = models.CharField(max_length=30)
    file_name = models.CharField(max_length=255)
    size_bytes = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["model", "object_id", "field"],
                name="uniq_stored_file_slot",
            ),
        ]
        indexes = [
            models.Index(fields=["file_name"], name="storedfile_name_idx"),
        ]

    def __str__(self):
        return f"StoredFile({self.model}#{self.object_id}.{self.field}, {self.size_bytes}B)"


class SubscriptionReminder(TimestampedModel):
    """BE-08. One row per reminder sent, so it is never sent twice."""

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="reminders"
    )
    event = models.CharField(max_length=30)
    # The end date the reminder was about; a new period gets new reminders.
    period_end = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "event", "period_end"],
                name="uniq_sub_reminder",
            ),
        ]

    def __str__(self):
        return f"SubscriptionReminder({self.subscription_id}, {self.event})"
