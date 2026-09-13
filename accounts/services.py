"""
Service layer for the accounts app (Part P-017).

Architecture Section 8: views must never contain business logic — every
endpoint's real work happens in a services.py function the view merely
calls. This is the first app to establish that pattern; every future
endpoint in the project should follow the same shape (thin
generics.CreateAPIView/APIView -> services.<verb>_<noun>() wrapped in a
transaction).

register_user() is intentionally minimal, matching this part's own
scope decision (flagged explicitly, not a silent default):

- Does NOT create a BusinessProfile row for account_type="business".
  That model doesn't exist until Phase 4 (Part P-040) — attempting it
  here would mean building against a model that isn't there yet. The
  seam is left open on purpose: Phase 4's Part P-040/P-042 lets a
  Business user complete their profile (Trader/Factory type, business
  name, etc.) as a separate follow-up step, right after registration.
- Does NOT collect or store business_type (Trader/Factory) or a phone
  number — both deferred to that same Phase 4 profile-completion step,
  per the same scope decision (see PROJECT_PROGRESS.md's P-017 entry).
- Derives `username` from the validated email. accounts.models.User
  (Part P-016) extends AbstractUser without overriding USERNAME_FIELD,
  so a populated, unique `username` is still required even though this
  registration flow's entire input surface is email/password/
  account_type. Using the email itself keeps that requirement satisfied
  without introducing an undocumented username field on the public API
  — flagged here, and in PROJECT_PROGRESS.md, as an explicit scope
  resolution rather than a silent deviation.

Kept as a keyword-only, narrow function signature on purpose: Phase 4
will need to call a related service function immediately after this one
returns (e.g. to attach a BusinessProfile), and a stable, minimal
register_user() is easier to build on top of than one already carrying
extra optional parameters this part doesn't need.
"""

from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


def register_user(*, email, password, account_type):
    """
    Create and return a new User for a Customer or Business signup.

    Wrapped in transaction.atomic() per Section 8's service-layer rule.
    Currently a single INSERT, but kept as an explicit transaction so
    this function's behavior doesn't need to change the moment a future
    part (e.g. Phase 4) needs it to do more than one write.

    Callers are expected to have already validated `email` (uniqueness,
    format) and `password` (strength) — see accounts.serializers.
    RegisterSerializer. This function does not re-validate either.
    """
    with transaction.atomic():
        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
            account_type=account_type,
        )
    return user


def authenticate_user(*, email, password):
    """
    Authenticate a login attempt by email + password (Part P-018).

    accounts.models.User (P-016) never overrode USERNAME_FIELD, and
    P-017's register_user() (above) sets `username` equal to the exact
    email string given at registration time. This function bridges the
    public, email-based login API to Django's real username-based auth
    backend:

      1. Look the user up by case-insensitive email — mirrors
         RegisterSerializer.validate_email's own case-insensitive
         uniqueness check, so a user who registered as
         `Foo@Example.com` and later logs in as `foo@example.com`
         still authenticates.
      2. Call Django's authenticate() with that user's *real*,
         exact-case `username`, not the (possibly different-case)
         email the caller passed in — ModelBackend's username lookup
         is case-sensitive, so passing the caller's raw input here
         could fail even for a correct password.

    Returns the authenticated User on success, or None on any failure
    (unknown email, wrong password, or an inactive account — Django's
    ModelBackend already refuses to authenticate is_active=False users
    and returns None for that case too). Never raises for "user not
    found" vs "wrong password" — both look identical to the caller, by
    design (no user-enumeration signal).
    """
    try:
        candidate = User.objects.get(email__iexact=email)
    except User.DoesNotExist:
        return None
    return authenticate(username=candidate.username, password=password)


def issue_token_pair(user):
    """
    Issue a fresh (access, refresh) JWT pair for a user (Part P-018).

    Thin wrapper around simplejwt's RefreshToken.for_user() so both
    LoginView and any future part that needs to mint tokens for a user
    (e.g. a future social-login part) call one shared function rather
    than constructing RefreshToken directly in multiple views.
    """
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}


def blacklist_refresh_token(raw_token):
    """
    Blacklist a refresh token immediately (Part P-018's LogoutView).

    This is in addition to, not instead of, the rotation-driven
    blacklisting ROTATE_REFRESH_TOKENS/BLACKLIST_AFTER_ROTATION already
    provide (config/settings/base.py's SIMPLE_JWT): without this
    explicit call, a token presented at logout would still work for one
    more /auth/refresh/ call before rotation retired it. Calling
    .blacklist() here makes logout immediate rather than waiting for a
    future rotation attempt to fail.

    Raises rest_framework_simplejwt.exceptions.TokenError (via
    RefreshToken(raw_token) or .blacklist() itself) for a malformed,
    expired, or already-blacklisted token — callers (accounts.views.
    LogoutView) are expected to catch this and translate it into a
    standard DRF ValidationError so it comes back in Part P-012's
    envelope shape instead of propagating as an unhandled exception.
    """
    token = RefreshToken(raw_token)
    token.blacklist()
