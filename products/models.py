"""
Part P-031 - Product + ProductVariant.

NON-TRANSACTIONAL BY DESIGN
---------------------------
Architecture Section 1/20 is unambiguous: this platform has NO cart,
NO checkout and NO payment flow anywhere in the MVP. A Product exists
so a customer can DISCOVER it, gauge whether it fits their budget, and
then message the business directly - the deal itself happens entirely
outside the platform.

Therefore this model must never grow:
  - a stock/inventory count field,
  - a SKU-as-purchasable-unit concept,
  - any cart / order / checkout / payment-adjacent field.

This is not a simplification to be filled in later. It is a permanent
architectural boundary for this MVP. If a future part appears to need
one of the above, that is a scope question to raise, not a field to
add quietly.

Ownership chain
---------------
Product FKs to businesses.BusinessProfile, never directly to
accounts.User - per the architecture's ER diagram (Section 9) and the
convention P-024 established for every content model.
"""

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models

from core.models import SoftDeleteModel, TimestampedModel


class Product(TimestampedModel, SoftDeleteModel):
    """A single item a Trader/Factory displays for discovery.

    Inherits SoftDeleteModel per P-011's convention for content models:
    `.objects` hides soft-deleted rows, `.delete()` is soft, and
    `.all_objects` / `.hard_delete()` remain available for deliberate
    admin cleanup only.
    """

    CURRENCY_EGP = "EGP"
    CURRENCY_SAR = "SAR"
    CURRENCY_AED = "AED"
    CURRENCY_JOD = "JOD"
    # Deliberately NOT hardcoded to EGP - this is a MENA-wide platform
    # (assumption A3), same reasoning as P-024's separate country/city
    # fields and P-027's country-agnostic phone validation. Extending
    # this set later is a one-line change plus a migration; adding
    # currencies nobody asked for now would widen the contract that
    # P-032 (serializer), P-033 (Flutter currency picker) and Phase 11
    # (search filters) all have to match exactly.
    CURRENCY_CHOICES = [
        (CURRENCY_EGP, "Egyptian Pound"),
        (CURRENCY_SAR, "Saudi Riyal"),
        (CURRENCY_AED, "UAE Dirham"),
        (CURRENCY_JOD, "Jordanian Dinar"),
    ]

    # on_delete=PROTECT, deliberately NOT CASCADE: BusinessProfile is
    # soft-deleted in normal operation (P-024), so a real, hard DELETE
    # of a BusinessProfile row is always an exceptional admin action.
    # PROTECT makes that action fail loudly rather than silently taking
    # every one of the business's Products down with it - the exact
    # audit/legal-protection concern architecture Section 9 raises for
    # soft delete in general.
    business = models.ForeignKey(
        "businesses.BusinessProfile",
        on_delete=models.PROTECT,
        related_name="products",
    )
    # on_delete=PROTECT, matching P-025's own Category.parent
    # convention. Note the deliberate difference from
    # BusinessProfile.category (P-026), which uses SET_NULL: that FK is
    # optional, this one is required - a Product with no category is
    # invisible to the category browse/filter flows that are the whole
    # point of Discovery (Section 20), so SET_NULL would be a silent
    # data-quality hole rather than a graceful degradation.
    category = models.ForeignKey(
        "categories.Category",
        on_delete=models.PROTECT,
        related_name="products",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    # Informational/negotiable price only - no purchase flow consumes
    # this field. Never wire this into a cart, order, or checkout
    # concept (architecture Section 20). It exists so a customer can
    # judge budget fit before messaging the business; the real,
    # final price is agreed off-platform.
    price = models.DecimalField(max_digits=10, decimal_places=2)
    # No default on purpose: defaulting to EGP would quietly re-centre
    # the platform on one market, which is exactly what A3 rules out.
    # The owning business must state its own currency explicitly.
    #
    # Known limitation, flagged not hidden: Django enforces `choices`
    # only through full_clean() / forms / serializers - never at the
    # database level. A direct Product.objects.create(currency="XYZ")
    # would be accepted by Postgres. P-032's ProductSerializer is
    # therefore the layer that MUST reject invalid currencies on the
    # real write path.
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES)
    # Part P-032: single, optional primary product image.
    #
    # Deliberately a plain FileField, not ImageField - Pillow is not a
    # project dependency (see requirements.txt) and never needs to
    # become one for this: the project's real image-content validation
    # already happens through core.media.validate_upload() via
    # python-magic content-sniffing (P-013's handoff note explicitly
    # requires every upload-handling part to route through that single
    # function), not through Django's own Pillow-based ImageField
    # validation. Adding Pillow just to get a redundant second
    # validation path would be an unjustified new dependency.
    #
    # Deliberately a single field, not a ProductImage child model / real
    # multi-image gallery: this part's own execution prompt explicitly
    # allows "a simple first-pass single primary image field ... if a
    # full multi-image gallery feels like scope creep for MVP", and a
    # real gallery (ordering, multiple files per request, per-image
    # delete) is a meaningfully larger feature. Flagged, not hidden: a
    # future part should build a real ProductImage(product, file,
    # position) model if/when multi-image galleries are actually
    # required - do not silently bolt a list of files onto this field.
    #
    # No default `upload_to` subfolder logic beyond "products/" - actual
    # storage (bucket/provider) is entirely delegated to
    # core.storage_backends.MediaStorage via STORAGES["default"]
    # (config/settings/base.py), matching P-013's convention that no
    # FileField/ImageField in this project should hardcode a storage
    # backend of its own.
    image = models.FileField(upload_to="products/", null=True, blank=True)
    # Business-controlled visibility toggle ("Hide" in the Trader app,
    # presentation slide 16) - distinct from is_deleted, which is the
    # generic soft-delete flag inherited from SoftDeleteModel.
    is_active = models.BooleanField(default=True)
    # Part P-063 (Phase 11, ADR-003): denormalized full-text search
    # vector over name + description. Kept in sync exclusively by
    # search/signals.py's post_save handler (STEP 3) via a direct
    # .update() on the queryset - never written to from model code,
    # a serializer, or a view. Null until the first save/signal run.
    search_vector = SearchVectorField(null=True, blank=True)

    class Meta:
        verbose_name = "Product"
        verbose_name_plural = "Products"
        ordering = ["-created_at"]
        indexes = [
            # Supports the "products in category X, optionally narrowed
            # to business Y" search/filter path from Sections 9/20.
            # Column order matters: category is the higher-selectivity,
            # more frequently filtered-alone column, so it leads.
            models.Index(
                fields=["category", "business"],
                name="products_category_business",
            ),
            # Part P-063: GIN index over search_vector, per architecture
            # Section 9's indexing guidance for Postgres full-text
            # search columns. Required for SearchQuery lookups against
            # this table to be fast rather than a sequential scan.
            GinIndex(fields=["search_vector"], name="products_search_vector_gin"),
        ]

    def __str__(self):
        return self.name


class ProductVariant(TimestampedModel):
    """A single descriptive option on a Product, e.g. Size=Large.

    Deliberately a plain key/value pair and NOT an inventory/SKU
    system: there is no order flow anywhere in this MVP to decrement a
    stock count against, so a quantity field here would be dead,
    misleading state. It exists purely so a business can DESCRIBE what
    it offers.

    Inherits TimestampedModel only, not SoftDeleteModel - per the part
    spec. A variant has no independent lifecycle: soft-deleting its
    parent Product already removes it from every read path.
    """

    # CASCADE is correct here, unlike the Product->BusinessProfile FK
    # above: a variant genuinely has no meaning without its product.
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    name = models.CharField(max_length=100)
    value = models.CharField(max_length=100)

    class Meta:
        verbose_name = "Product Variant"
        verbose_name_plural = "Product Variants"
        ordering = ["id"]

    def __str__(self):
        return f"{self.name}: {self.value}"