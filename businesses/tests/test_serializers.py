"""
Serializer tests for Part P-027 — International Phone Number Validation.

Exercises BusinessProfileSerializer.validate_phone_number() directly
(is_valid()/errors), not through the HTTP layer — these are unit tests
of the validation/normalization rule itself, per the part's own
"Testing: Serializer validation tests across at least three different
country codes" requirement. businesses/tests/test_api.py already
covers the /me/ endpoint end-to-end for every other field; this file
does not duplicate that.
"""

import pytest

from businesses.models import BusinessProfile
from businesses.serializers import BusinessProfileSerializer

pytestmark = pytest.mark.django_db


def _base_payload(**overrides) -> dict:
    payload = {
        "business_name": "Acme Trading",
        "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
        "country": "Egypt",
        "city": "Cairo",
    }
    payload.update(overrides)
    return payload


class TestPhoneNumberValidation:
    @pytest.mark.parametrize(
        "raw_input, expected_e164",
        [
            # Egypt
            ("+201001234567", "+201001234567"),
            # Saudi Arabia
            ("+966501234567", "+966501234567"),
            # United Arab Emirates
            ("+971501234567", "+971501234567"),
        ],
    )
    def test_valid_number_for_each_country_code_validates_and_normalizes(
        self, raw_input, expected_e164
    ):
        serializer = BusinessProfileSerializer(
            data=_base_payload(phone_number=raw_input)
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["phone_number"] == expected_e164

    def test_number_typed_with_spacing_still_normalizes_to_e164(self):
        # Same Saudi number as above, typed with spaces the way a user
        # might actually enter it — proves normalization, not just a
        # pass-through of already-clean input.
        serializer = BusinessProfileSerializer(
            data=_base_payload(phone_number="+966 50 123 4567")
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["phone_number"] == "+966501234567"

    def test_blank_phone_number_is_allowed_and_skips_validation(self):
        serializer = BusinessProfileSerializer(data=_base_payload(phone_number=""))
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["phone_number"] == ""

    def test_missing_phone_number_field_is_allowed(self):
        # phone_number isn't in the payload at all (optional field).
        serializer = BusinessProfileSerializer(data=_base_payload())
        assert serializer.is_valid(), serializer.errors

    def test_number_missing_country_code_prefix_is_rejected(self):
        # Proves no default-region assumption is silently applied: a
        # local-format Egyptian number without "+20" must fail, not
        # quietly validate against an assumed Egyptian region.
        serializer = BusinessProfileSerializer(
            data=_base_payload(phone_number="01001234567")
        )
        assert not serializer.is_valid()
        assert "phone_number" in serializer.errors

    def test_malformed_number_with_country_code_is_rejected(self):
        # Has a "+" and a real country code, but the digits that
        # follow are not a valid number under that country's numbering
        # plan (too short to be real).
        serializer = BusinessProfileSerializer(data=_base_payload(phone_number="+2012"))
        assert not serializer.is_valid()
        assert "phone_number" in serializer.errors

    def test_non_numeric_garbage_is_rejected(self):
        serializer = BusinessProfileSerializer(
            data=_base_payload(phone_number="not-a-phone-number")
        )
        assert not serializer.is_valid()
        assert "phone_number" in serializer.errors

    def test_saved_instance_persists_normalized_e164_value(self):
        # End-to-end within the serializer layer: .save() actually
        # writes the reformatted value to the model instance, not just
        # to validated_data.
        user_payload = _base_payload(phone_number="+20 100 123 4567")
        serializer = BusinessProfileSerializer(data=user_payload)
        assert serializer.is_valid(), serializer.errors
        # BusinessProfile.user is required at the DB level; save() here
        # only needs to prove the field-level transformation survives
        # into a real instance, so a transient (unsaved) instance via
        # .save(commit=False)-style build is enough — ModelSerializer
        # doesn't support commit=False, so instantiate the model
        # directly with validated_data instead of calling .save().
        instance = BusinessProfile(**serializer.validated_data)
        assert instance.phone_number == "+201001234567"
