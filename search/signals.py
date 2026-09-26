"""
Part P-063 (Phase 11, ADR-003) - keeps Product.search_vector and
BusinessProfile.search_vector in sync on every create/update.

Recursion guard
----------------
A naive handler that did `instance.search_vector = SearchVector(...)`
then `instance.save()` would re-fire this same post_save signal on the
same sender and loop forever. Both handlers below avoid that entirely
by never calling `.save()` (or anything else that emits save-related
signals) on the instance: they issue a direct
`<Model>.objects.filter(pk=instance.pk).update(search_vector=...)`.
QuerySet.update() is a plain UPDATE statement - Django deliberately
does NOT send post_save for it, so there is no loop to guard against,
not "a loop that happens not to trigger in practice."

Field names are hardcoded per model (name/description for Product,
business_name/description for BusinessProfile) - verified directly
against products/models.py (P-031) and businesses/models.py (P-024)
rather than assumed, per this part's own execution prompt.
"""

from django.contrib.postgres.search import SearchVector
from django.db.models.signals import post_save
from django.dispatch import receiver

from businesses.models import BusinessProfile
from products.models import Product


@receiver(post_save, sender=Product)
def update_product_search_vector(sender, instance, **kwargs):
    Product.objects.filter(pk=instance.pk).update(
        search_vector=SearchVector("name", "description")
    )


@receiver(post_save, sender=BusinessProfile)
def update_business_profile_search_vector(sender, instance, **kwargs):
    BusinessProfile.objects.filter(pk=instance.pk).update(
        search_vector=SearchVector("business_name", "description")
    )