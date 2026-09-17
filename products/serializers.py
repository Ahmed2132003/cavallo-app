"""
Serializers for Part P-032 — Product CRUD (Business-Owned, IDOR-Protected)
+ Media Upload.

IDOR note (same structural pattern as businesses/serializers.py, P-026):
``business`` is deliberately listed in ``read_only_fields`` below, not
left off ``fields`` entirely, so it still renders on read (the caller
needs to see which business owns a public product) while remaining
completely non-writable. Any ``business``/``business_id`` key sent in a
request body is not a writable field, so DRF drops it silently during
validation — it can never reach the view/service layer no matter what a
client sends. Ownership is assigned exclusively in
``products/views.py``'s ``ProductListCreateView.perform_create()``, from
``request.user.business_profile`` — never from client input. This is
the "many-owned-resources" application of P-026's own proven pattern
(see that part's handoff note and this part's own module docstring in
views.py).

Currency validation gap closure (P-031's own flagged, deliberate gap):
Django's ``choices=`` on a model field is enforced by ``full_clean()``,
never at the raw ``.save()``/DB level (P-031's
``test_direct_save_does_not_enforce_choices_known_gap`` documents this).
DRF's ``ModelSerializer`` auto-generates a ``ChoiceField`` for any model
field that declares ``choices``, so routing every write through this
serializer (as every view in this part does) closes that gap for the
real write path with no extra code — see
``TestProductCurrencyValidation`` in ``tests/test_api.py`` for the test
that locks this in.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from core.media import validate_upload

from .models import Product, ProductVariant

# Part P-013's own convention (see core/tests/test_media.py and its
# handoff note): a fixed, explicit allow-list per upload site, never a
# shared "any image" wildcard. 5 MB matches the part spec's own
# suggested reasonable default for a single product image.
_ALLOWED_IMAGE_MIME_TYPES = ["image/jpeg", "image/png", "image/webp"]
_MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


class ProductVariantSerializer(serializers.ModelSerializer):
    """Read-only nested representation of a Product's variants.

    Deliberately read-only here: this part's own execution prompt scopes
    ProductSerializer to "all P-031 fields, plus an images field" only —
    variant creation/editing is not part of P-032's scope and is left
    for a future part to add as its own explicit write path (e.g. a
    nested-list endpoint), rather than silently smuggled in through this
    serializer. Exposing them read-only here costs nothing and lets a
    product's existing variants (however they were created, e.g. via
    Django Admin) show up in the public/API representation today.
    """

    class Meta:
        model = ProductVariant
        fields = ["id", "name", "value"]
        read_only_fields = ["id", "name", "value"]


class ProductSerializer(serializers.ModelSerializer):
    """
    Write (via the owning business only — see views.py): category, name,
    description, price, currency, image, is_active.

    Read adds: id (read-only), business (read-only — see module
    docstring), created_at/updated_at (read-only), variants (read-only
    nested list, see ProductVariantSerializer above).

    Deliberately the single serializer class for every read path
    (owner's own list, owner's own detail, public detail, public list)
    — mirrors businesses/serializers.py's own reasoning: nothing on
    Product is sensitive enough to need a separate "public" subset (no
    raw User/BusinessProfile internals are ever exposed, just the
    BusinessProfile's own id).
    """

    variants = ProductVariantSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "business",
            "category",
            "name",
            "description",
            "price",
            "currency",
            "image",
            "is_active",
            "variants",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "business", "created_at", "updated_at"]

    def validate_image(self, value):
        """
        Part P-013 integration (per that part's own handoff note): call
        validate_upload() from inside a serializer's validate_<field>()
        method so DRF converts the resulting
        django.core.exceptions.ValidationError into DRF's own
        ValidationError automatically, which is what
        core.exceptions.custom_exception_handler (P-012) then reshapes
        into the project's standard {"error": {...}} envelope. No
        separate, ad hoc MIME/size check is implemented here — this is
        the single shared choke point P-013 requires every
        upload-handling part to reuse.

        ``value`` is None/absent whenever the client omits the image
        entirely (an optional field, per the model) — skip validation
        in that case exactly like BusinessProfileSerializer's
        validate_phone_number() (P-027) skips its own optional field.
        """
        if not value:
            return value

        try:
            validate_upload(
                value,
                allowed_mime_types=_ALLOWED_IMAGE_MIME_TYPES,
                max_size_bytes=_MAX_IMAGE_SIZE_BYTES,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))

        return value
