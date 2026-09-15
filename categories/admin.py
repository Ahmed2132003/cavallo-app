from django.contrib import admin

from categories.models import Category


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    """Admin-only management surface for the category tree.

    This is an internal Admin-tooling decision, independent of the
    moderator-review-UI decision Ahmed made for content moderation
    (Post/Reel/Story/Product reports) — the two are not related, and
    this part does not build (or need) a public write endpoint at all
    (see categories/urls.py: only the read-only tree endpoint exists).
    """

    list_display = ("name", "parent", "is_active", "slug", "created_at")
    list_filter = ("is_active", "parent")
    search_fields = ("name", "slug")
    # Lets an Admin pick a parent by typing/searching rather than
    # scrolling a giant <select> as the tree grows — relies on
    # search_fields above (Django Admin's autocomplete requirement).
    autocomplete_fields = ("parent",)
    readonly_fields = ("created_at", "updated_at")
    ordering = ("name",)
