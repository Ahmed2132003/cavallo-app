"""
Part P-089 (STEP 1): payment-side records.

NAMING DISTINCTION (read this before touching either model):

* ``payments.Subscription`` (this file) is the COMMERCIAL / PAYMENT
  record. It tracks the lifecycle of ONE payment attempt for a plan:
  pending -> completed / failed.
* ``monetization.FeaturedSubscription`` (P-086) is the PRODUCT-STATE
  record. It says whether a business is Featured right now, from when
  until when.

A completed payment (P-090) is what leads to a FeaturedSubscription,
and ONLY through ``monetization.services.activate_subscription()``.
Nothing in this app creates or changes a FeaturedSubscription.

PCI SCOPE: this system never stores card or payment-instrument data.
The user pays on a gateway-hosted page (redirect via ``payment_url``)
and we only receive a confirmation. Do NOT add any field that could
hold card numbers, CVVs, expiry dates or card tokens.
"""

from django.db import models

from core.models import TimestampedModel
from products.models import Product

STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

STATUS_CHOICES = [
    (STATUS_PENDING, "Pending"),
    (STATUS_COMPLETED, "Completed"),
    (STATUS_FAILED, "Failed"),
]


class Subscription(TimestampedModel):
    """
    Payment-side record of one purchase attempt (NOT the Featured state;
    see the module docstring and monetization.FeaturedSubscription).
    """

    business = models.ForeignKey(
        "businesses.BusinessProfile",
        on_delete=models.CASCADE,
        related_name="payment_subscriptions",
    )
    # PROTECT: a Plan cannot be deleted while a payment record uses it.
    plan = models.ForeignKey(
        "monetization.Plan",
        on_delete=models.PROTECT,
        related_name="payment_subscriptions",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    # The gateway's own reference for this attempt (for Paymob: the
    # order id of the created intention). Blank until the gateway
    # assigns one.
    gateway_reference = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
    )

    def __str__(self):
        return (
            f"payments.Subscription({self.pk}) business={self.business_id} "
            f"status={self.status}"
        )


class Transaction(TimestampedModel):
    """
    One gateway transaction belonging to a payment Subscription.

    ``transaction_id`` is the gateway's own transaction identifier and is
    what the webhook idempotency check (P-090) keys on. It is UNIQUE and
    NULLABLE: when the payment is initiated the gateway has not created a
    transaction yet, so the row starts with NULL, and the webhook fills it
    in. PostgreSQL treats NULLs as distinct, so any number of pending rows
    can coexist while real ids stay unique.
    """

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    transaction_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    # Same list as Product / Plan (P-031 / P-086), never a second copy.
    currency = models.CharField(max_length=3, choices=Product.CURRENCY_CHOICES)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    def __str__(self):
        return (
            f"Transaction({self.pk}) subscription={self.subscription_id} "
            f"status={self.status}"
        )


class Invoice(TimestampedModel):
    """
    Invoice for a completed payment. PDF generation is optional for
    P-089, so ``pdf_url`` is nullable.
    """

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    pdf_url = models.URLField(max_length=500, null=True, blank=True)

    def __str__(self):
        return f"Invoice({self.pk}) subscription={self.subscription_id}"
