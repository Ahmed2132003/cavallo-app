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

from django.contrib.auth import get_user_model
from django.db import transaction

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
