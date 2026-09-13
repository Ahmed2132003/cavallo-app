"""
Permission-based authorization framework (Part P-019).

Architecture Section 4 mandates PERMISSION-based checks for Admin/
Moderator-tier actions, not role-based checks scattered as
``if user.role == "admin"`` (or ``if user.is_staff``) everywhere — so a
Moderator can hold partial capability without becoming a full Admin.

This uses Django's own built-in Permission/Group system (battle-tested,
free Django Admin integration for managing Groups/Permissions) rather
than a custom permission-flag model. The concrete permission codenames
are defined on ``accounts.models.User`` (see that model's ``Meta.
permissions`` and its own docstring) because the real moderation-
specific models (``ModerationQueue``, etc.) don't exist until Phase 6 —
Phase 6 may add moderation-app-specific permissions alongside these
without anything here needing to change.

``HasCapability(codename)`` is a DRF permission-class *factory*: it
returns a class (not an instance), matching how every other
``permission_classes`` entry in this project is written — e.g.
``permission_classes = [HasCapability("can_moderate_content")]``.

Simple "is this a Business account" checks for basic feature access are
NOT what this framework targets — those stay as plain
``request.user.account_type == "business"``-style checks in the
relevant view/serializer. This framework is specifically for
Admin/Moderator-tier capability checks.
"""

from rest_framework.permissions import BasePermission


def HasCapability(codename):
    """
    Return a DRF permission class gating access on a named capability.

    The returned class's ``has_permission`` checks
    ``request.user.has_perm(f"accounts.{codename}")`` — Django's own
    ``has_perm`` already accounts for ``is_superuser`` (a superuser
    implicitly has every permission) and for group membership (a user
    in a Group has every permission assigned to that Group), so no
    additional Group-membership lookup is needed here.

    Usage::

        class SomeAdminOnlyView(APIView):
            permission_classes = [HasCapability("can_moderate_content")]

    :param codename: one of the codenames defined in
        ``accounts.models.User.Meta.permissions`` (e.g.
        ``"can_moderate_content"``), WITHOUT the ``"accounts."`` app-label
        prefix — this factory adds that prefix itself.
    """

    class _HasCapability(BasePermission):
        message = f"Missing required capability: {codename}."

        def has_permission(self, request, view):
            user = getattr(request, "user", None)
            if not user or not user.is_authenticated:
                return False
            return user.has_perm(f"accounts.{codename}")

    _HasCapability.__name__ = f"HasCapability_{codename}"
    _HasCapability.__qualname__ = _HasCapability.__name__
    return _HasCapability
