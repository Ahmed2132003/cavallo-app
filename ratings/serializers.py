"""Serializers for Part P-109 - Rating submission and listing."""

from rest_framework import serializers

from ratings.models import Rating


class RateBusinessSerializer(serializers.Serializer):
    """Input validation for POST /api/v1/businesses/{id}/rate/.

    A plain (non-Model) input serializer, matching this project's
    convention for write endpoints whose target is resolved from the
    URL, not the body (see reports.ReportCreateSerializer /
    social.CommentCreateSerializer).
    """

    score = serializers.IntegerField(min_value=1, max_value=5)
    review_text = serializers.CharField(
        required=False, allow_blank=True, default=""
    )


class RatingSerializer(serializers.ModelSerializer):
    """Public representation of a single Rating, for
    GET /api/v1/businesses/{id}/ratings/.

    ``customer`` is deliberately the plain FK id (matches
    social.CommentSerializer's own "user" field) - no separate public
    user-summary serializer exists yet in this project to reuse; a
    future part can extend this if a richer author display (name/
    avatar) is ever required.
    """

    class Meta:
        model = Rating
        fields = ("id", "customer", "score", "review_text", "created_at")
        read_only_fields = fields


class RateBusinessResponseSerializer(serializers.Serializer):
    """Output shape for POST /api/v1/businesses/{id}/rate/ - the
    business's freshly recomputed aggregate, not the Rating row
    itself (the caller already knows what they just submitted).
    """

    average_rating = serializers.DecimalField(max_digits=3, decimal_places=2)
    ratings_count = serializers.IntegerField()
