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

import phonenumbers
from phonenumbers import NumberParseException
from rest_framework import serializers

from .models import BusinessProfile, CustomerProfile


class BusinessProfileSerializer(serializers.ModelSerializer):
    """
    Write: business_name, business_type, country, city, description,
    category (category added by this part — see models.py's P-026
    comment on the field; optional/nullable both at the DB level and
    here, via required=False from ModelSerializer's blank=True
    inference), phone_number (added by P-027 — optional, validated
    and normalized to E.164 by validate_phone_number() below; see
    models.py's P-027 comment on the field).

    Read adds: id (read-only), is_verified (computed, read-through
    property — see models.py), follower_count (Part P-052: a real,
    atomically-updated denormalized counter — read-only here; only
    ever mutated by social/views.py's FollowToggleView via F()
    expressions, never through this serializer).
    """

    is_verified = serializers.BooleanField(read_only=True)

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
            "phone_number",
            "is_verified",
            "follower_count",
        ]
        read_only_fields = ["id", "follower_count"]

    def validate_phone_number(self, value):
        """
        Part P-027. Optional field — an empty value (the "no phone on
        file" state, see models.py) skips validation entirely and is
        returned as-is.

        phonenumbers.parse(value, None) deliberately passes None as
        the default region: this forces every input to carry its own
        explicit country code (e.g. +966501234567) instead of silently
        assuming Egypt (or any other single country), per architecture
        assumption A3's explicit MENA-wide requirement. A number
        missing the leading "+" raises NumberParseException here,
        which is the intended behavior, not a bug to work around.
        """
        if not value:
            return value

        try:
            parsed = phonenumbers.parse(value, None)
        except NumberParseException:
            raise serializers.ValidationError(
                "Enter a valid phone number including the country "
                "code, e.g. +201234567890."
            )

        if not phonenumbers.is_valid_number(parsed):
            raise serializers.ValidationError(
                "Enter a valid phone number including the country "
                "code, e.g. +201234567890."
            )

        # Reformat to E.164 so the stored value is always consistent
        # regardless of how the business typed it in (spacing,
        # parentheses, dashes, etc. are all normalized away).
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


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
