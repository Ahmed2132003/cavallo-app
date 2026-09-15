from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from categories.models import Category
from categories.views import CATEGORY_TREE_CACHE_KEY


@receiver(post_save, sender=Category)
@receiver(post_delete, sender=Category)
def invalidate_category_tree_cache(sender, **kwargs):
    """Deletes the cached 'categories:tree' entry immediately on any
    Category create/edit/delete (including Django Admin), so the public
    tree endpoint reflects an Admin's change right away instead of
    waiting up to the full ~1h TTL — mandatory per this part's own spec,
    not optional.

    Deliberately calls ``django.core.cache.cache.delete(...)`` directly
    rather than going through ``core.cache.cache_get_or_set`` — that
    function's contract is "read-or-compute-and-store", it has no
    invalidation mode. This is the one sanctioned exception to core/
    cache.py's own "no future part should call cache.get/cache.set
    directly" convention (P-014's handoff note), since *delete* is a
    different operation from get/set and there's no ad hoc key string
    involved (it reuses the exact constant the view writes under).
    Flagging for Ahmed: if this pattern recurs in later parts, it's
    probably worth adding a proper ``cache_delete(key)`` helper to
    core/cache.py instead of repeating this same justification.
    """
    cache.delete(CATEGORY_TREE_CACHE_KEY)
