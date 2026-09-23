"""
Views for Part P-052 — Follow/Unfollow (idempotent) + atomic
denormalized counters.

This is the first genuine application of architecture Section 5 rule
4 (never COUNT() a live table) under real concurrent-write risk. Both
counters (BusinessProfile.follower_count, User.following_count) are
mutated EXCLUSIVELY via .filter(pk=...).update(field=F(field) + 1/-1)
— never instance.field = instance.field + 1 followed by .save(),
which races under concurrent requests (classic read-then-write bug).

Idempotency contract:
- POST (follow): following an already-followed business returns 200
  with no duplicate Follow row and no double-increment.
- DELETE (unfollow): unfollowing a business you don't follow is a
  harmless no-op — still 200, never an error, counters untouched.

Assumption (flagged, per this part's own spec instruction): the
architecture doesn't say whether Business-type accounts may follow
other businesses. This view allows ANY authenticated user regardless
of account_type — see social/models.py's Follow docstring.
"""

from django.db import transaction
from django.db.models import F
from django.contrib.auth import get_user_model
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from businesses.models import BusinessProfile

from .models import Follow

User = get_user_model()


def _get_business_or_404(pk):
    # Mirrors content/views.py's _get_post_or_404 /
    # stories/views.py's _get_story_or_404 convention — a plain
    # DRF-NotFound helper, not a queryset-level 404 (keeps the error
    # in this project's standard {"error": {...}} envelope, not a raw
    # Django Http404 with an HTML body).
    try:
        return BusinessProfile.objects.get(pk=pk)
    except BusinessProfile.DoesNotExist:
        raise NotFound("Business not found.")


class FollowToggleView(APIView):
    """
    POST   /api/v1/businesses/{id}/follow/ — follow (idempotent)
    DELETE /api/v1/businesses/{id}/follow/ — unfollow (idempotent)
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        business = _get_business_or_404(pk)

        with transaction.atomic():
            _follow, created = Follow.objects.get_or_create(
                follower=request.user, business=business
            )
            if created:
                BusinessProfile.objects.filter(pk=business.pk).update(
                    follower_count=F("follower_count") + 1
                )
                User.objects.filter(pk=request.user.pk).update(
                    following_count=F("following_count") + 1
                )

        return Response({"following": True})

    def delete(self, request, pk):
        business = _get_business_or_404(pk)

        with transaction.atomic():
            deleted_count, _ = Follow.objects.filter(
                follower=request.user, business=business
            ).delete()
            if deleted_count:
                BusinessProfile.objects.filter(
                    pk=business.pk, follower_count__gt=0
                ).update(follower_count=F("follower_count") - 1)
                User.objects.filter(pk=request.user.pk, following_count__gt=0).update(
                    following_count=F("following_count") - 1
                )

        return Response({"following": False})
