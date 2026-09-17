from django.contrib import admin

from .models import Product, ProductVariant


class ProductVariantInline(admin.TabularInline):
    """Lets an Admin view/edit a Product's variants on the same page,
    instead of navigating to a separate ProductVariant list - variants
    have no independent lifecycle outside their parent Product (see
    models.py)."""

    model = ProductVariant
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "business",
        "category",
        "price",
        "currency",
        "is_active",
        "created_at",
    )
    list_filter = ("currency", "is_active", "category")
    search_fields = ("name", "business__business_name")
    # Lets an Admin pick a business/category by typing/searching rather
    # than scrolling a giant <select> as either table grows - relies on
    # ProductAdmin/BusinessProfileAdmin/CategoryAdmin's own
    # search_fields (Django Admin's autocomplete requirement), same
    # pattern categories/admin.py already established for Category.parent.
    autocomplete_fields = ("business", "category")
    readonly_fields = ("created_at", "updated_at")
    inlines = [ProductVariantInline]


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    """Standalone admin kept alongside the inline above - useful for a
    direct search across variants (e.g. "every product with a Size:
    Large variant") without drilling into each Product individually."""

    list_display = ("product", "name", "value", "created_at")
    search_fields = ("name", "value", "product__name")
    autocomplete_fields = ("product",)
    readonly_fields = ("created_at", "updated_at")
