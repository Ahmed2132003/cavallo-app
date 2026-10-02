"""
Part P-094 (Phase 17) - cache invalidation for the public Business Profile.

BusinessProfilePublicView caches its response for 5 minutes under
"business_profile:{pk}" (Part P-030), and BusinessProfile.is_verified is a
read-through to User.is_business_verified (Part P-024). Admin verification
happens on the User row, so without this receiver an Admin toggling
"is_business_verified" in Django Admin would not be reflected on the public
profile (the Verified badge) for up to 5 minutes. Found by the P-094
integration pass (finding F-2).

This receiver deletes the cached public profile whenever a business User is
saved. Django Admin saves go through Model.save(), which fires post_save.
(A bulk QuerySet.update() does not fire signals; no code path in this project
uses one for verification.)
"""

from django.core.cache import cache
from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import User


@receiver(post_save, sender=User, dispatch_uid="businesses_invalidate_profile_cache")
def invalidate_business_profile_cache_on_user_save(sender, instance, **kwargs):
    # Imported lazily: businesses.views imports a lot at module load time.
    from businesses.models import BusinessProfile
    from businesses.views import _business_profile_cache_key

    profile_id = (
        BusinessProfile.objects.filter(user_id=instance.pk)
        .values_list("pk", flat=True)
        .first()
    )
    if profile_id is not None:
        cache.delete(_business_profile_cache_key(profile_id))