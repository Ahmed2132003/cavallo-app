"""
Unit tests for ProductVariantWriteSerializer — Part P-032B.

Serializer-only tests, no DB and no view/URL layer yet: only the
serializer's own field-level validation is exercised here in
isolation. Ownership resolution and the actual create/update/delete
HTTP endpoints are covered separately once views.py/urls.py exist
(later steps of this same part).
"""

from products.serializers import ProductVariantWriteSerializer


class TestProductVariantWriteSerializerValidation:
    def test_valid_name_and_value_are_accepted(self):
        serializer = ProductVariantWriteSerializer(
            data={"name": "Size", "value": "Large"}
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["name"] == "Size"
        assert serializer.validated_data["value"] == "Large"

    def test_missing_name_is_rejected(self):
        serializer = ProductVariantWriteSerializer(data={"value": "Large"})

        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_missing_value_is_rejected(self):
        serializer = ProductVariantWriteSerializer(data={"name": "Size"})

        assert not serializer.is_valid()
        assert "value" in serializer.errors

    def test_blank_name_after_stripping_whitespace_is_rejected(self):
        serializer = ProductVariantWriteSerializer(
            data={"name": "   ", "value": "Large"}
        )

        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_name_over_max_length_is_rejected(self):
        serializer = ProductVariantWriteSerializer(
            data={"name": "x" * 101, "value": "Large"}
        )

        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_id_is_read_only_and_ignored_on_input(self):
        serializer = ProductVariantWriteSerializer(
            data={"id": 999, "name": "Size", "value": "Large"}
        )

        assert serializer.is_valid(), serializer.errors
        assert "id" not in serializer.validated_data
