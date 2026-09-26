"""Rating upsert logic (Part P-109).

Architecture note - deliberate departure from Phase 9's pattern
--------------------------------------------------------------
Every Phase 9 counter (BusinessProfile.follower_count, Comment.
reports_count, etc.) is maintained with a pure F()-increment/decrement
inside .filter(pk=...).update(...), because those events are purely
additive or toggle-style (see social/views.py's FollowToggleView).

A Rating is neither: it is an UPSERT. When a customer updates their
existing score, a naive F()-increment approach would need to first
subtract the OLD score's contribution from the running average before
adding the new one - mathematically awkward and easy to get subtly
wrong. Given ratings are a low-frequency action compared to
Likes/Follows, the simpler, provably-correct approach for MVP is to
recompute the average/count fresh from the Rating table's actual rows
on every write, inside the same transaction as the upsert. Do not
"fix" this into an F()-increment later without re-deriving the correct
math for the update case.
"""

from django.db import transaction
from django.db.models import Avg, Count

from ratings.models import Rating


def rate_business(*, customer, business, score, review_text=""):
    """Create or update ``customer``'s rating of ``business``.

    Returns the ``Rating`` instance (created or updated) together with
    the business's freshly recomputed ``average_rating`` /
    ``ratings_count``, applied atomically:

    1. ``Rating.objects.update_or_create(...)`` - one row per
       (customer, business), per the model's ``unique_together``. A
       second call from the same customer updates the existing row's
       ``score``/``review_text`` rather than creating a duplicate.
    2. The business's aggregate is recomputed FRESH from every Rating
       row currently on that business (``Avg``/``Count``), never
       incrementally adjusted - see module docstring for why.
    3. ``BusinessProfile.average_rating`` / ``ratings_count`` are
       written via ``.filter(pk=...).update(...)`` (never
       ``instance.save()``), matching this project's standard
       denormalized-counter convention (architecture Section 5 rule
       4: never read-then-write a counter under concurrent-request
       risk).

    Avoids a circular import with ``businesses.models`` at module load
    time by importing it inside the function body.
    """
    from businesses.models import BusinessProfile

    with transaction.atomic():
        rating, created = Rating.objects.update_or_create(
            customer=customer,
            business=business,
            defaults={"score": score, "review_text": review_text},
        )

        aggregates = Rating.objects.filter(business=business).aggregate(
            avg=Avg("score"), count=Count("id")
        )
        average_rating = aggregates["avg"] or 0
        ratings_count = aggregates["count"]

        BusinessProfile.objects.filter(pk=business.pk).update(
            average_rating=average_rating,
            ratings_count=ratings_count,
        )

    return rating, created, average_rating, ratings_count
