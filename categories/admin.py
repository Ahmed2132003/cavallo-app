from django import forms
from django.contrib import admin

from categories.models import Category


class CategoryAdminForm(forms.ModelForm):
    """Lets staff enter the English and Arabic names (Part P-112).

    ``name`` stays the English/fallback column, so it is no longer
    required on its own: when ``name_en`` is given, ``name`` follows it.
    """

    class Meta:
        model = Category
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].required = False
        self.fields["name"].help_text = "English / fallback name (follows English)."

    def clean(self):
        cleaned = super().clean()
        name = (cleaned.get("name") or "").strip()
        name_en = (cleaned.get("name_en") or "").strip()
        english = name_en or name
        if not english:
            raise forms.ValidationError("Enter at least the English name.")
        cleaned["name"] = english
        cleaned["name_en"] = english
        return cleaned


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    """Admin-only management surface for the category tree.

    This is an internal Admin-tooling decision, independent of the
    moderator-review-UI decision Ahmed made for content moderation
    (Post/Reel/Story/Product reports) — the two are not related, and
    this part does not build (or need) a public write endpoint at all
    (see categories/urls.py: only the read-only tree endpoint exists).
    """

    form = CategoryAdminForm
    list_display = ("name_en", "name_ar", "parent", "is_active", "slug", "created_at")
    list_filter = ("is_active", "parent")
    search_fields = ("name", "name_ar", "name_en", "slug")
    # Lets an Admin pick a parent by typing/searching rather than
    # scrolling a giant <select> as the tree grows — relies on
    # search_fields above (Django Admin's autocomplete requirement).
    autocomplete_fields = ("parent",)
    readonly_fields = ("created_at", "updated_at")
    ordering = ("name",)
