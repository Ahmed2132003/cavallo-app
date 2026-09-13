"""
Custom User model + role fields (Part P-016).

Architecture Section 4 requires 5 roles: Customer, Business, Moderator,
Admin, Super Admin — and requires authorization to be PERMISSION-based,
not just role-based (no scattered ``if user.role == "admin"`` checks).
The full permission-flag enforcement system is built in Part P-019; this
module only establishes the fields that system (and everything else in
the project) will read.

Field-to-role mapping (locked contract — see the class docstring below
and Part P-019's handoff notes; do not change this mapping silently):

    is_superuser         -> Super Admin   (built into AbstractUser)
    is_staff              -> Admin-capable (built into AbstractUser)
    is_moderator          -> Moderator     (new field, this part)
    account_type="business" -> Business    (new field, this part)
    account_type="customer" -> Customer    (new field, this part; default)

Deliberately NOT done here, and left to later parts:
    - BusinessProfile / CustomerProfile 1:1 extension models (Phase 4,
      Part P-040) — this part is the User row only.
    - Permission-flag enforcement (e.g. custom DRF permission classes
      reading these fields) — Part P-019.

Part P-019 update: ``Meta.permissions`` on the ``User`` model below
defines the initial capability set (``can_moderate_content``,
``can_manage_categories``, ``can_ban_users``,
``can_manage_notifications``, ``can_manage_monetization``) on THIS
model, because the real moderation-specific models
(``ModerationQueue``, etc.) don't exist until Phase 6. These live
under the ``accounts`` app label, so a check looks like
``request.user.has_perm("accounts.can_moderate_content")`` (see
``core.permissions.HasCapability``). Phase 6 will additionally
define moderation-app-specific permissions once ``ModerationQueue``
exists — both sets can coexist; nothing here needs to change when
that happens.
"""

from django.contrib.auth.models import AbstractUser
from django.db import models

from core.models import TimestampedModel


class User(AbstractUser, TimestampedModel):
    """
    Custom user model, set as AUTH_USER_MODEL from the project's very
    first migration (required — swapping AUTH_USER_MODEL after real
    migrations/data exist is extremely painful, which is why this is
    Part P-016, first thing in Phase 3, before anything else references
    User).

    Deliberately extends AbstractUser (not a from-scratch custom user)
    to reuse Django's existing, well-tested username/email/password/
    is_active/is_staff/is_superuser machinery rather than reinventing
    it. TimestampedModel adds created_at/updated_at.

    Deliberately does NOT also inherit core.models.SoftDeleteModel:
    architecture doesn't require soft-deleting user accounts the way it
    requires it for content (posts/reels/products/...). If a
    "deactivate, don't destroy" semantic is ever needed for accounts,
    use Django's built-in is_active=False pattern instead — simpler,
    and it doesn't collide with AbstractUser's own use of is_active for
    authentication (an inactive user already can't log in).

    Role fields, and why each one is (or isn't) new:

    - ``is_staff`` / ``is_superuser`` (from AbstractUser, unchanged):
      reused to represent Admin / Super Admin respectively, rather than
      adding redundant new boolean fields that would need to be kept in
      sync with Django's own auth/permission machinery (which already
      keys off these two fields everywhere — Django Admin login,
      ``has_perm``, etc.).
    - ``is_moderator`` (new): the one genuinely new role flag Django
      has no built-in equivalent for.
    - ``account_type`` (new): distinguishes Customer vs Business at the
      base-account level, per architecture Section 4's table. This is
      orthogonal to is_staff/is_superuser/is_moderator — an Admin or
      Super Admin account still carries an account_type value (see the
      field's own docstring below for why, and what value it gets).
    - ``is_business_verified`` (new): corresponds to Section 4's
      "Verify" admin action on businesses. Only meaningful when
      ``account_type == "business"``; always False for customer
      accounts and never surfaced/actioned for them.

    P-019's permission-flag system, and every future admin-action part,
    should read these five attributes (is_staff, is_superuser,
    is_moderator, account_type, is_business_verified) rather than
    inventing a parallel role representation.
    """

    ACCOUNT_TYPE_CUSTOMER = "customer"
    ACCOUNT_TYPE_BUSINESS = "business"
    ACCOUNT_TYPE_CHOICES = [
        (ACCOUNT_TYPE_CUSTOMER, "Customer"),
        (ACCOUNT_TYPE_BUSINESS, "Business"),
    ]

    account_type = models.CharField(
        max_length=20,
        choices=ACCOUNT_TYPE_CHOICES,
        default=ACCOUNT_TYPE_CUSTOMER,
        help_text=(
            "Customer vs Business (Section 4). Never blank; defaults to "
            "'customer' so createsuperuser needs no extra prompt — see "
            "the User class docstring for the full rationale."
        ),
    )
    is_moderator = models.BooleanField(
        default=False,
        help_text="Moderator sub-role (architecture Section 4). Independent of "
        "account_type and of is_staff/is_superuser.",
    )
    is_business_verified = models.BooleanField(
        default=False,
        help_text="Section 4's 'Verify' admin action on businesses. Only "
        "meaningful when account_type='business'; stays False and unused "
        "for customer accounts.",
    )

    class Meta:
        db_table = "accounts_user"
        # Part P-019: initial capability set, defined here (on User)
        # rather than on a moderation-specific model, since
        # ModerationQueue/etc. don't exist until Phase 6. Do not
        # rename/remove these codenames silently — every future
        # Admin/Moderator-facing endpoint checks one of these exact
        # strings via core.permissions.HasCapability(). Phase 6 may
        # ADD moderation-app-specific permissions alongside these;
        # it should not need to change this list.
        permissions = [
            ("can_moderate_content", "Can moderate content"),
            ("can_manage_categories", "Can manage categories"),
            ("can_ban_users", "Can ban users"),
            ("can_manage_notifications", "Can manage notifications"),
            ("can_manage_monetization", "Can manage monetization"),
        ]

    def __str__(self):
        return self.username
