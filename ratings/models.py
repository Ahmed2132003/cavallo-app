"""
Part P-109 - Rating model.

A Rating is a customer's current opinion of a business - singular, per
customer, per business. Unlike Like/Save/Share/Follow (Phase 9's
purely-additive or toggle-style events), a customer does not create a
new Rating each time they rate a business again; they UPDATE their one
existing row (unique_together = ("customer", "business")). This is
the same distinction P-056 drew for Share's own, opposite choice
(genuinely repeatable, never unique_together) - flagged here so a
future engineer doesn't "fix" this into a repeatable-event shape.

review_text is plain user content, not Moderatable: abusive reviews
are handled by reporting the Rating row through the existing generic
Report mechanism (reports app, P-057/P-058), not a parallel
moderation path built specifically for reviews.

BusinessProfile.average_rating / ratings_count (added by this part's
additive migration on businesses/models.py) are the denormalized
read path Search's rating filter (P-064) and any future
business-profile "4.5 (120 reviews)" display consume. They are NEVER
computed from a live COUNT()/AVG() on read (architecture Section 5
rule 4) - only ratings.services.rate_business() may write them, always
via .filter(pk=...).update(...) inside the same transaction as the
Rating upsert. See that function's docstring for why a full aggregate
recompute (not an F()-increment) is the correct approach here.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from core.models import TimestampedModel


class Rating(TimestampedModel):
    """A customer's current 1-5 score (+ optional review text) for a
    business. One row per (customer, business) pair - see module
    docstring.

    Deliberately does NOT inherit SoftDeleteModel: this part's spec
    defines no delete/retraction flow for a rating (only create/
    update via the upsert endpoint), matching the "no scope beyond
    what's asked" instruction. If a future part needs "remove my
    rating," that is a new part, not a field to add quietly here.
    """

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ratings_given",
    )
    business = models.ForeignKey(
        "businesses.BusinessProfile",
        on_delete=models.CASCADE,
        related_name="ratings",
    )
    score = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    review_text = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Rating"
        verbose_name_plural = "Ratings"
        ordering = ["-created_at"]
        unique_together = (("customer", "business"),)
        indexes = [
            # Supports GET /api/v1/businesses/{id}/ratings/ - listing
            # a single business's reviews, newest first.
            models.Index(
                fields=["business", "-created_at"],
                name="ratings_rating_business_idx",
            ),
        ]

    def __str__(self):
        return f"{self.customer_id} -> {self.business_id}: {self.score}"
