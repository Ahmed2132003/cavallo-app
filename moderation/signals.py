from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save
from django.dispatch import receiver

from moderation.models import Moderatable, ModerationQueue


@receiver(post_save)
def enqueue_new_moderatable_content(sender, instance, created, **kwargs):
    """
    Creates exactly one ModerationQueue row the first time any
    Moderatable-mixin model instance is saved — UNLESS that instance's
    class opts out via ``auto_enqueue_on_create = False`` (Part P-042's
    deferred-enqueue hook; see Moderatable's docstring for the full
    rationale). Instances that opt out are responsible for creating
    their own ModerationQueue row explicitly once they reach a
    reviewable state (e.g. Reel, once transcoding finishes).

    Deliberately connected WITHOUT a ``sender=`` argument (i.e. to every
    model's post_save, filtered here via ``isinstance``) rather than
    wired per-model. This is the whole point of building this as a
    mixin + generic signal instead of a save() override: Phase 7's
    Post/Reel and Phase 8's Story models get moderation enqueueing for
    free just by inheriting Moderatable — nobody building those models
    later needs to remember to add a matching signal registration, and
    there is exactly one code path to audit for the Section 28
    "moderation bypass" risk instead of one per content type. Reel's
    P-042 exception is handled via the ``auto_enqueue_on_create`` class
    attribute (checked below with ``getattr``), NOT by adding an
    ``isinstance(instance, Reel)`` check here — this module must never
    import or know about a specific content type.

    The `isinstance` check is cheap (a single Python-side type check on
    every model save in the project, not a DB query) and correct: it
    matches any current or future subclass of Moderatable regardless of
    which concrete app defines it, without moderation/ needing to import
    or know about Post/Reel/Story at all.

    ``created`` (from Django's own post_save signal, which distinguishes
    INSERT from UPDATE) is what enforces "enqueue on first creation
    only, not on every edit" — re-review-on-edit policy is explicitly
    out of scope for this part (see models.py's Moderatable docstring
    and this part's spec) and is NOT silently implemented here.
    """

    if not created:
        return
    if not isinstance(instance, Moderatable):
        return
    if not getattr(instance, "auto_enqueue_on_create", True):
        return

    ModerationQueue.objects.create(
        content_type=ContentType.objects.get_for_model(instance),
        object_id=instance.pk,
    )