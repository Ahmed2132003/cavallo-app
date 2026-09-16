"""
Serializers for Part P-026 — Business/Customer Profile CRUD.

Both serializers are plain ModelSerializers used for BOTH read (GET,
including the public view) and write (POST onboarding / PATCH update):
the same class renders the full representation on output and validates
the writable subset of fields on input. There is deliberately no
separate "public" serializer — nothing on BusinessProfile is sensitive
enough to hide from an anonymous viewer (no raw User fields, no
internal verification-review notes), so BusinessProfilePublicView
(businesses/views.py) reuses this exact class per the part's own
acceptance criteria.

IDOR note (Section 5 rule 10): neither serializer declares `id` or
`user` as a writable field. That is not an oversight — it is the other
half of the structural IDOR mitigation alongside
businesses/views.py's get_object() logic. Any `id`/`user_id` key sent
in a request body is silently dropped by DRF during validation (it is
simply not a declared field), so it can never reach
services.update_business_profile()/update_customer_profile() no matter
what a client sends. Do NOT add `id` or `user` as writable fields to
either serializer later without re-reading businesses/views.py's
IDOR-mitigation docstring first.
"""

from rest_framework import serializers

from .models import BusinessProfile, CustomerProfile


class BusinessProfileSerializer(serializers.ModelSerializer):
    """
    Write: business_name, business_type, country, city, description,
    category (category added by this part — see models.py's P-026
    comment on the field; optional/nullable both at the DB level and
    here, via required=False from ModelSerializer's blank=True
    inference).

    Read adds: id (read-only), is_verified (computed, read-through
    property — see models.py), follower_count (placeholder, always 0
    until Phase 9 wires a real Follow counter).
    """

    is_verified = serializers.BooleanField(read_only=True)
    follower_count = serializers.SerializerMethodField()

    class Meta:
        model = BusinessProfile
        fields = [
            "id",
            "business_name",
            "business_type",
            "country",
            "city",
            "description",
            "category",
            "is_verified",
            "follower_count",
        ]
        read_only_fields = ["id"]

    def get_follower_count(self, obj) -> int:
        # TODO(Phase 9): wire to real Follow counter. Defaulting to 0
        # is deliberate per this part's explicit scope — the Follow
        # model/relationship doesn't exist yet.
        return 0


class CustomerProfileSerializer(serializers.ModelSerializer):
    """Write: display_name, country, city. Read adds: id (read-only).

    No public view exists for CustomerProfile (see views.py) — this
    serializer is only ever used against request.user's own profile,
    so it carries no analogue of BusinessProfileSerializer's
    is_verified/follower_count read-only additions.
    """

    class Meta:
        model = CustomerProfile
        fields = ["id", "display_name", "country", "city"]
        read_only_fields = ["id"]
