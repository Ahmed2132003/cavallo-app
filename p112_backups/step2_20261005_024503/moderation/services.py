"""
Moderation state-machine service (Part P-037, notifications added in P-079).

approve() and reject() are the ONLY sanctioned way any code may move a
Moderatable content object's ``status`` to "published" or "rejected".

RULE FOR PHASE 7 (Posts/Reels) AND PHASE 8 (Stories): content models,
views, serializers, admin actions and Celery tasks must NEVER write to a
Moderatable model's ``status`` field directly to publish or reject
something. Always call the functions in this module. Setting the field
anywhere else bypasses the audit trail (ModerationLog) and reintroduces
the Section 28 "moderation bypass" risk that this whole app exists to
prevent.

Each function performs the full state transition in ONE transaction:

1. the ModerationQueue row's ``status``,
2. the underlying content object's ``status`` (via the generic FK),
3. a new ModerationLog row,

all three or none. If any step fails, nothing is persisted.

This module contains no permission checks and no HTTP concerns.

Part P-079: AFTER the transaction has committed, the content's business
owner is notified through notifications.tasks.enqueue_notification (the
single notification orchestration path; never call
create_notification/send_push_notification from here). The notification
is best-effort: a failure to enqueue is logged and never affects the
moderation decision that has already been committed. This module still
imports no specific content model: the owner and the deep link are
resolved generically from ``content.business`` and the model name.
"""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction

from moderation.models import Moderatable, ModerationLog, ModerationQueue
from notifications.tasks import enqueue_notification

logger = logging.getLogger(__name__)

# model_name -> deep_link_type (target_id = the content's own id).
# Anything not listed (today: story) has no detail screen in the P-078
# contract, so it deep-links to the owning business profile instead.
_CONTENT_DEEP_LINKS = {
    "post": "post_detail",
    "reel": "reel_detail",
}


class AlreadyDecidedError(ValidationError):
    """
    Raised when approve()/reject() is called on a queue item that is no
    longer pending.

    A subclass of ValidationError so callers may catch either the
    specific case (e.g. the API layer mapping it to a 409 Conflict) or
    the general validation error. It is raised instead of silently
    re-processing or silently doing nothing, so a double submit (e.g. a
    double-tap in a UI) can never create two audit-log entries for one
    decision.
    """


def _lock_pending_queue_item(queue_item):
    """
    Re-read the queue row with a row lock and confirm it is still pending.

    MUST be called inside ``transaction.atomic()``: select_for_update()
    only holds its lock for the life of the surrounding transaction.

    The status is checked on the freshly locked row, NOT on the
    ``queue_item`` object the caller passed in. That object may be stale
    (loaded before another request decided the same item), and trusting
    it would let two concurrent requests both see "pending".
    """
    locked = ModerationQueue.objects.select_for_update().get(pk=queue_item.pk)

    if locked.status != ModerationQueue.Status.PENDING:
        raise AlreadyDecidedError(
            f"Moderation queue item {locked.pk} has already been "
            f"decided (status: {locked.status}); it cannot be processed again."
        )

    return locked


def _get_moderatable_content(locked_queue_item):
    """
    Resolve the queue row's generic FK and confirm it is a live
    Moderatable object. Raises ValidationError otherwise (for example,
    the referenced object was hard-deleted).
    """
    content = locked_queue_item.content_object

    if content is None:
        raise ValidationError(
            f"Moderation queue item {locked_queue_item.pk} references a "
            f"content object that no longer exists."
        )
    if not isinstance(content, Moderatable):
        raise ValidationError(
            f"Moderation queue item {locked_queue_item.pk} references an "
            f"object that is not Moderatable."
        )

    return content


def _notify_content_owner(content, approved, reason=""):
    """
    Tell the content's business owner about the moderation decision
    (Part P-079). Best-effort: never raises.

    Content without a ``business`` (for example P-036's throwaway
    DummyContent) has nobody to notify, so nothing is sent.
    """
    try:
        business = getattr(content, "business", None)
        owner_id = getattr(business, "user_id", None)
        if owner_id is None:
            return

        model_name = content._meta.model_name
        deep_link_type = _CONTENT_DEEP_LINKS.get(model_name)
        if deep_link_type:
            target_id = content.pk
        else:
            deep_link_type = "business_profile"
            target_id = business.pk

        if approved:
            notification_type = "moderation_approved"
            title = f"Your {model_name} was approved"
            body = f"Your {model_name} is now published."
        else:
            notification_type = "moderation_rejected"
            title = f"Your {model_name} was rejected"
            body = f"Reason: {reason}"

        enqueue_notification(
            recipient_id=owner_id,
            notification_type=notification_type,
            title=title,
            body=body,
            deep_link_type=deep_link_type,
            target_id=target_id,
        )
    except Exception:
        logger.exception(
            "Moderation notification could not be prepared for %r; the "
            "moderation decision is unaffected.",
            content,
        )


def approve(queue_item, reviewer):
    """
    Approve a pending queue item: the content object becomes
    "published". Returns the created ModerationLog row.

    Raises AlreadyDecidedError if the item is not pending; in that case
    nothing is changed and no log row is created.
    """
    with transaction.atomic():
        locked = _lock_pending_queue_item(queue_item)
        content = _get_moderatable_content(locked)

        locked.status = ModerationQueue.Status.APPROVED
        locked.save()

        content.status = Moderatable.Status.PUBLISHED
        content.save()

        log = ModerationLog.objects.create(
            queue_item=locked,
            reviewer=reviewer,
            action=ModerationLog.Action.APPROVED,
            reason="",
        )

    # Only reached if the transaction committed: keep the caller's
    # in-memory object in sync with the database.
    queue_item.status = locked.status

    _notify_content_owner(content, approved=True)
    return log


def reject(queue_item, reviewer, reason):
    """
    Reject a pending queue item: the content object becomes "rejected".
    Returns the created ModerationLog row.

    ``reason`` is required and must not be blank: a rejection without a
    reason is useless to the business owner who receives it. The check
    happens BEFORE any database access, so an invalid call touches
    nothing.

    Raises ValidationError for a blank reason, and AlreadyDecidedError
    if the item is not pending; in both cases nothing is changed, no log
    row is created and no notification is sent.
    """
    cleaned_reason = (reason or "").strip()
    if not cleaned_reason:
        raise ValidationError("A rejection reason is required.")

    with transaction.atomic():
        locked = _lock_pending_queue_item(queue_item)
        content = _get_moderatable_content(locked)

        locked.status = ModerationQueue.Status.REJECTED
        locked.save()

        content.status = Moderatable.Status.REJECTED
        content.save()

        log = ModerationLog.objects.create(
            queue_item=locked,
            reviewer=reviewer,
            action=ModerationLog.Action.REJECTED,
            reason=cleaned_reason,
        )

    queue_item.status = locked.status

    _notify_content_owner(content, approved=False, reason=cleaned_reason)
    return log
