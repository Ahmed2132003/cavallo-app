"""Serializers for Part P-109 - Rating submission and listing."""

from rest_framework import serializers

from accounts.serializers import UserPublicSerializer
from ratings.models import Rating


class RateBusinessSerializer(serializers.Serializer):
    """Input validation for POST /api/v1/businesses/{id}/rate/.

    A plain (non-Model) input serializer, matching this project's
    convention for write endpoints whose target is resolved from the
    URL, not the body (see reports.ReportCreateSerializer /
    social.CommentCreateSerializer).
    """

    score = serializers.IntegerField(min_value=1, max_value=5)
    review_text = serializers.CharField(required=False, allow_blank=True, default="")


class RatingSerializer(serializers.ModelSerializer):
    """Public representation of a single Rating, for
    GET /api/v1/businesses/{id}/ratings/.

    ``customer`` is kept as the plain FK id (existing key, unchanged).
    Part P-116A-2c adds ``reviewer``: the reviewer's public display info
    (``display_name`` + ``avatar`` only) via accounts.UserPublicSerializer.
    The view must select_related the customer's profiles (see
    RatingsListView.get_queryset) to avoid an N+1.
    """

    reviewer = UserPublicSerializer(source="customer", read_only=True)

    class Meta:
        model = Rating
        fields = (
            "id",
            "customer",
            "reviewer",
            "score",
            "review_text",
            "created_at",
        )
        read_only_fields = fields


class RateBusinessResponseSerializer(serializers.Serializer):
    """Output shape for POST /api/v1/businesses/{id}/rate/ - the
    business's freshly recomputed aggregate, not the Rating row
    itself (the caller already knows what they just submitted).
    """

    average_rating = serializers.DecimalField(max_digits=3, decimal_places=2)
    ratings_count = serializers.IntegerField()
