"""
Part P-024 — BusinessProfile + CustomerProfile.

These are the 1:1 profile extensions that Part P-017 (registration)
deliberately left unbuilt: a Business-type or Customer-type User row
already exists, but neither has a real profile yet. Every later
content model (Product, Post, Reel, Story) FKs to BusinessProfile,
never directly to User, per the architecture's ER diagram (Section 9).

Verification single source of truth
------------------------------------
BusinessProfile does NOT carry its own is_verified database field.
User.is_business_verified (added on the User model in P-016, toggled
by an Admin "Verify" action per Section 4) is the only place
verification state is stored. `BusinessProfile.is_verified` below is a
plain read-through property so the two values can never drift apart.

Account-type values
--------------------
"customer" / "business" are the exact wire values confirmed against
accounts.models.User.account_type in P-016/P-017/P-020 (User.ACCOUNT_TYPE_CHOICES).
Written here as plain string literals rather than imported constants
since no such named constants were documented as existing on User;
if accounts.models.User does expose named choice constants, switch
these literals (and the ones in services.py) to reference them
directly instead of duplicating the raw strings.
"""

from django.conf import settings
from django.db import models

from core.models import SoftDeleteModel, TimestampedModel


class BusinessProfile(TimestampedModel, SoftDeleteModel):
    BUSINESS_TYPE_TRADER = "trader"
    BUSINESS_TYPE_FACTORY = "factory"
    BUSINESS_TYPE_CHOICES = [
        (BUSINESS_TYPE_TRADER, "Trader"),
        (BUSINESS_TYPE_FACTORY, "Factory"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="business_profile",
    )
    business_name = models.CharField(max_length=255)
    business_type = models.CharField(max_length=20, choices=BUSINESS_TYPE_CHOICES)
    # country/city are deliberately two separate CharFields, never one
    # combined free-text location field — explicit A3 requirement
    # (architecture Section 20), so filtering by country alone stays
    # possible for every later part (search/filter, P-026, etc.).
    country = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    # Part P-026 addition: resolves the gap P-025 explicitly flagged and
    # left open (see PROJECT_PROGRESS.md's P-025 entry, "Gap carried
    # forward to P-026"). Architecture Section 9's ER diagram shows
    # Category (1)──(M) BusinessProfile, which P-024 could not add
    # because the categories app didn't exist yet at that point.
    # Nullable/optional by design — a Business can complete onboarding
    # via POST /me/ before ever picking a category, and assign/change
    # one later via PATCH /me/. on_delete=SET_NULL (not PROTECT, unlike
    # Category's own self-FK): deleting a category must never block
    # deleting/keeping a business profile the way deleting a parent
    # category with live children is blocked.
    category = models.ForeignKey(
        "categories.Category",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="business_profiles",
    )
    # Part P-027 addition: optional contact number (architecture
    # assumption A3 - a Trader/Factory's contact number matters more
    # than for Customers, which is why this lives on BusinessProfile
    # only, not CustomerProfile). Validated and normalized at the
    # serializer layer (businesses/serializers.py's
    # validate_phone_number()) using the phonenumbers library against
    # the number's own embedded country code - never a hardcoded
    # Egypt-only pattern, since this is a MENA-wide platform (+20,
    # +966, +971, etc). Stored value is always E.164
    # (e.g. +201234567890), regardless of how the business typed it
    # in. blank=True/default="" (not null=True): an empty string is
    # the "no phone on file" state, matching description's convention
    # above, so there is exactly one representation of "not set".
    phone_number = models.CharField(max_length=20, blank=True, default="")
    # Part P-052 addition: denormalized, atomically-updated counter
    # (Section 5 rule 4 — never COUNT() a live table). Replaces the
    # BusinessProfileSerializer placeholder that always returned 0
    # (P-026's `# TODO(Phase 9)`). ONLY ever mutated via
    # BusinessProfile.objects.filter(pk=...).update(follower_count=F(...))
    # inside social/views.py's FollowToggleView — never via
    # instance.follower_count = ... + save(), which would race under
    # concurrent requests. Named `follower_count` (singular), matching
    # the wire contract P-026/P-028A already shipped to the mobile app
    # — NOT `followers_count`, despite the master-plan spec's literal
    # field name.
    follower_count = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Business Profile"
        verbose_name_plural = "Business Profiles"

    def __str__(self):
        return self.business_name

    @property
    def is_verified(self) -> bool:
        """
        Read-through to User.is_business_verified. Deliberately NOT a
        stored field — see module docstring. Never add a real
        is_verified column to this model; that would reintroduce the
        exact two-sources-of-truth drift this part exists to avoid.
        """
        return self.user.is_business_verified


class CustomerProfile(TimestampedModel, SoftDeleteModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="customer_profile",
    )
    display_name = models.CharField(max_length=255)
    country = models.CharField(max_length=100)
    city = models.CharField(max_length=100)

    class Meta:
        verbose_name = "Customer Profile"
        verbose_name_plural = "Customer Profiles"

    def __str__(self):
        return self.display_name
