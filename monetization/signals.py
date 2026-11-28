"""BE-02: auto-create the Free subscription when a BusinessProfile is created."""

from django.db.models.signals import post_save
from django.dispatch import receiver


# Lazy string sender: no import of businesses.models at module load,
# so no circular import.
@receiver(
    post_save,
    sender="businesses.BusinessProfile",
    dispatch_uid="monetization_create_free_subscription",
)
def create_free_subscription_on_business_created(
    sender, instance, created, raw=False, **kwargs
):
    if not created or raw:
        return
    # Imported lazily: services imports businesses.models.
    from monetization.services import create_free_subscription

    create_free_subscription(instance)