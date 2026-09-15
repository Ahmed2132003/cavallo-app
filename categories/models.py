from django.db import models
from django.utils.text import slugify

from core.models import TimestampedModel


class Category(TimestampedModel):
    """Self-referencing category tree (Products / BusinessProfiles filter
    and search by category, per architecture Section 20).

    Deliberately inherits ``core.models.TimestampedModel`` only, **not**
    ``core.models.SoftDeleteModel`` — a documented deviation from core's
    default "every future content model should inherit both" convention
    (P-011), not a silent omission:

    1. The part spec (P-025) explicitly lists only ``TimestampedModel``
       for this model.
    2. ``is_active`` already gives Admins the domain-level soft-toggle
       they need (hide a category from the public tree without deleting
       it) — a second, generic ``is_deleted`` flag on top of that would
       be redundant for this model specifically.
    3. Stacking ``SoftDeleteModel`` on a self-referencing tree with a
       globally-unique ``slug`` would complicate slug-uniqueness checks
       (a soft-deleted category would still occupy its slug under the
       default manager's exclusion semantics), for no real benefit here
       since ``on_delete=PROTECT`` already stops any parent with live
       children from being destroyed at all.

    Flag for Ahmed if this reasoning doesn't hold going forward.
    """

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "categories_category"
        verbose_name = "Category"
        verbose_name_plural = "Categories"
        # Two different branches may reasonably want a same-named leaf
        # (e.g. "Accessories" under both "Fashion" and "Electronics"),
        # per the execution prompt's own instruction — so uniqueness is
        # scoped to (parent, name), not a single global "name" column.
        #
        # Known edge case, not silently swept under the rug: in
        # PostgreSQL (and standard SQL generally), NULL is never equal
        # to NULL for uniqueness purposes, so this constraint does NOT
        # stop two different *root-level* categories (parent IS NULL)
        # from sharing the same name — only two children of the SAME
        # non-null parent are blocked. If root-level name-uniqueness is
        # actually required, it needs a separate partial/conditional
        # unique index (e.g. a `UniqueConstraint` with
        # `condition=Q(parent__isnull=True)`), which is out of this
        # part's explicit scope — flagging for Ahmed to decide, not
        # adding it silently.
        unique_together = (("parent", "name"),)
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._generate_unique_slug()
        super().save(*args, **kwargs)

    def _generate_unique_slug(self):
        base_slug = slugify(self.name)[:140]
        slug = base_slug
        suffix = 2
        # Excludes self.pk so re-saving an existing category (e.g. via
        # Django Admin, without touching name/slug) never collides with
        # its own current slug.
        while Category.objects.exclude(pk=self.pk).filter(slug=slug).exists():
            candidate_suffix = f"-{suffix}"
            slug = f"{base_slug[: 140 - len(candidate_suffix)]}{candidate_suffix}"
            suffix += 1
        return slug
