"""
Service-layer functions for the social app.

Part P-055 introduces the first function here:
`check_and_hide_if_threshold_exceeded` — the auto-hide seam for
Comment. It is a PURE service function: it does not know how
`reports_count` gets incremented. Part P-057 (Report) is the real
caller — it must increment `Comment.reports_count` (via
`.filter(pk=...).update(reports_count=F("reports_count") + 1)`) and
then call this function, rather than re-implementing the threshold
logic. Same seam pattern as P-038 consuming P-037's services.
"""

from django.utils import timezone

from .models import Comment

# PLACEHOLDER pending real-world tuning: the number of reports at which
# a Comment is auto-hidden. Deliberately a module-level constant (read
# at call time, so it can be monkeypatched in tests and later moved to
# settings/env without changing the function), matching the P-039
# SLA-threshold precedent. Do not treat 5 as a product-confirmed value.
COMMENT_AUTO_HIDE_THRESHOLD = 5


def check_and_hide_if_threshold_exceeded(comment: Comment) -> bool:
    """
    Auto-hide `comment` if its persisted `reports_count` has reached
    COMMENT_AUTO_HIDE_THRESHOLD.

    Returns True ONLY if THIS call is the one that hid the comment
    (so a caller such as P-057 can tell whether this report was the
    trigger). Returns False if the comment is below the threshold OR
    was already hidden.

    Implementation note (deliberate): this is a single conditional
    UPDATE against the database, not "read comment.reports_count then
    save()". The DB row is the source of truth (P-057 increments it
    with F(), which never touches the caller's in-memory instance),
    and the `is_hidden=False` condition makes concurrent callers safe:
    exactly one of them gets True.

    On success the passed-in instance's `is_hidden` is updated in
    memory too, so the caller sees the new state without a refresh.
    """
    updated = Comment.objects.filter(
        pk=comment.pk,
        is_hidden=False,
        reports_count__gte=COMMENT_AUTO_HIDE_THRESHOLD,
    ).update(is_hidden=True, updated_at=timezone.now())

    if updated:
        comment.is_hidden = True
        return True
    return False
