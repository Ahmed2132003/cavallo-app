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

import hashlib
import hmac
import logging
import re
import secrets

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.mail import send_mail
from django.db import transaction
from django_redis import get_redis_connection
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()
logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Password reset OTP core (Task P2.1)
#
# Scope: OTP issue + verify only. No email sending, reset tokens, API
# endpoints, or throttling here — later tasks build on these two functions.
#
# Redis stores only "<issuance_id>:<HMAC-SHA256 digest>" — never the raw
# OTP. The digest covers (normalized email, issuance id, OTP). Every
# issuance gets a fresh random issuance id, so two issuances can never
# produce the same stored value even if the six-digit OTP collides. Keys
# are derived from a SHA-256 hash of the normalized email, so no key ever
# contains an email address.
# ---------------------------------------------------------------------------

_REDIS_ALIAS = "default"
OTP_TTL_SECONDS = 300
OTP_MAX_ATTEMPTS = 5
_OTP_KEY_PREFIX = "pwreset:otp:"
_ATTEMPTS_KEY_PREFIX = "pwreset:attempts:"
_OTP_PATTERN = re.compile(r"[0-9]{6}")

# Issue/replace: store the new issuance value ("<issuance_id>:<digest>")
# with a 300s TTL and reset the attempt counter in one atomic step, so a
# reissue can never leave stale attempts attached to the new OTP (or the
# old issuance alive).
# KEYS[1] = otp key, KEYS[2] = attempts key
# ARGV[1] = stored issuance value, ARGV[2] = ttl seconds
_LUA_ISSUE = """
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
redis.call('SET', KEYS[2], 0, 'EX', ARGV[2])
return 1
"""

# Attempt + fetch: atomically check the issuance exists, reject if
# attempts are already at the maximum, otherwise increment the counter and
# return the complete stored issuance value (issuance id + digest). The
# attempts key's expiry is kept equal to the otp key's remaining TTL so
# both expire together.
# KEYS[1] = otp key, KEYS[2] = attempts key
# ARGV[1] = max attempts
# Returns {status, stored value}: status 0 = missing/expired, 2 = attempts
# exhausted, 1 = attempt counted and stored value returned.
_LUA_ATTEMPT = """
local digest = redis.call('GET', KEYS[1])
if not digest then
    return {0, ''}
end
local attempts = tonumber(redis.call('GET', KEYS[2]) or '0')
if attempts >= tonumber(ARGV[1]) then
    return {2, ''}
end
redis.call('INCR', KEYS[2])
local pttl = redis.call('PTTL', KEYS[1])
if pttl > 0 then
    redis.call('PEXPIRE', KEYS[2], pttl)
end
return {1, digest}
"""

# Consume: delete the OTP and attempts keys only if the complete stored
# issuance value (issuance id + digest) still equals the value that was
# fetched and verified. Only the caller whose script run actually performs
# the DEL gets 1; a concurrent consumer or a reissue in between — even one
# with an identical six-digit OTP — carries a different issuance id, so
# the comparison fails and returns 0.
# KEYS[1] = otp key, KEYS[2] = attempts key
# ARGV[1] = complete stored issuance value that was verified
_LUA_CONSUME = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('DEL', KEYS[1], KEYS[2])
    return 1
end
return 0
"""


def _normalize_email(email):
    return email.strip().casefold()


def _otp_keys(normalized_email):
    email_id = hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()
    return _OTP_KEY_PREFIX + email_id, _ATTEMPTS_KEY_PREFIX + email_id


def _otp_digest(normalized_email, issuance_id, otp):
    message = f"{normalized_email}:{issuance_id}:{otp}".encode("utf-8")
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


def _to_text(value):
    return value.decode("ascii") if isinstance(value, bytes) else value


def _run_script(conn, source, keys, args):
    return conn.register_script(source)(keys=keys, args=args)


def _consume_otp(conn, otp_key, attempts_key, stored_value):
    """
    Atomically delete the OTP only if the complete stored issuance value
    (issuance id + digest) is unchanged.
    """
    return (
        int(_run_script(conn, _LUA_CONSUME, [otp_key, attempts_key], [stored_value]))
        == 1
    )


def create_password_reset_otp(email):
    """
    Issue a password-reset OTP for `email` and return the raw six-digit
    string (needed by a later task to email it to the user).

    Replaces any previous OTP for the same normalized email and resets
    the attempt counter, atomically. Each issuance gets a fresh random
    issuance id; Redis stores only "<issuance_id>:<HMAC-SHA256 digest>"
    with a 300-second expiry. The raw OTP is never stored or logged.
    """
    normalized = _normalize_email(email)
    otp = f"{secrets.randbelow(10**6):06d}"
    issuance_id = secrets.token_hex(16)
    stored_value = f"{issuance_id}:{_otp_digest(normalized, issuance_id, otp)}"
    otp_key, attempts_key = _otp_keys(normalized)
    conn = get_redis_connection(_REDIS_ALIAS)
    _run_script(
        conn,
        _LUA_ISSUE,
        [otp_key, attempts_key],
        [stored_value, OTP_TTL_SECONDS],
    )
    return otp


def verify_password_reset_otp(email, otp):
    """
    Verify `otp` for `email`. Returns True exactly once per issued OTP.

    Returns False for: a malformed OTP (not exactly six ASCII digits —
    this does not consume an attempt), a missing/expired/already-used
    OTP, a wrong OTP, or any attempt after the fifth.
    """
    if not isinstance(otp, str) or _OTP_PATTERN.fullmatch(otp) is None:
        return False

    normalized = _normalize_email(email)
    otp_key, attempts_key = _otp_keys(normalized)
    conn = get_redis_connection(_REDIS_ALIAS)

    status, stored = _run_script(
        conn, _LUA_ATTEMPT, [otp_key, attempts_key], [OTP_MAX_ATTEMPTS]
    )
    if int(status) != 1:
        return False

    stored_value = _to_text(stored)
    issuance_id, separator, stored_digest = stored_value.rpartition(":")
    if not separator:
        return False
    supplied = _otp_digest(normalized, issuance_id, otp)
    if not hmac.compare_digest(
        stored_digest.encode("ascii"), supplied.encode("ascii")
    ):
        return False

    return _consume_otp(conn, otp_key, attempts_key, stored_value)


# ---------------------------------------------------------------------------
# Password reset token core (Task P2.2)
#
# Scope: reset-token issue + consume only. No email sending, API endpoints,
# password change, or throttling here — later tasks build on these functions.
#
# A reset token is only ever minted by verify_otp_and_issue_reset_token(),
# which first consumes the OTP through verify_password_reset_otp() (P2.1,
# unchanged), so an OTP can never be reused and the token cannot be issued
# without a successful verification. The raw token (256 bits from
# secrets.token_urlsafe) is returned to the caller once and never stored or
# logged: Redis holds only an HMAC-SHA256 digest over (normalized email,
# token), under a key derived from a SHA-256 hash of the normalized email.
# One active token per email — issuing a new one replaces the previous.
# ---------------------------------------------------------------------------

RESET_TOKEN_TTL_SECONDS = 300
_RESET_TOKEN_KEY_PREFIX = "pwreset:token:"
_RESET_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")  # token_urlsafe(32)

# Consume: delete the token key only if the stored digest equals the digest
# of the supplied token. The compare and the delete run as one atomic Redis
# step, so of any number of concurrent consumers exactly one gets 1. The
# comparison is not constant-time, which is safe here: the stored value is a
# keyed HMAC of the supplied token, so a caller cannot steer it byte by byte.
# KEYS[1] = token key
# ARGV[1] = digest of the supplied token
_LUA_CONSUME_RESET_TOKEN = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('DEL', KEYS[1])
    return 1
end
return 0
"""


def _reset_token_key(normalized_email):
    email_id = hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()
    return _RESET_TOKEN_KEY_PREFIX + email_id


def _reset_token_digest(normalized_email, token):
    message = f"reset-token:{normalized_email}:{token}".encode("utf-8")
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


def _issue_reset_token(normalized_email):
    """
    Mint a reset token for an already-verified normalized email. Private on
    purpose: the only public way to get a token is
    verify_otp_and_issue_reset_token().
    """
    token = secrets.token_urlsafe(32)
    conn = get_redis_connection(_REDIS_ALIAS)
    conn.set(
        _reset_token_key(normalized_email),
        _reset_token_digest(normalized_email, token),
        ex=RESET_TOKEN_TTL_SECONDS,
    )
    return token


def verify_otp_and_issue_reset_token(email, otp):
    """
    Verify (and thereby consume) the password-reset OTP for `email`; on
    success return a fresh raw reset token (a 43-character URL-safe string)
    valid for RESET_TOKEN_TTL_SECONDS. Returns None on any OTP failure and
    issues nothing.

    The OTP is consumed by verify_password_reset_otp() before the token is
    minted, so the same OTP can never produce a second token. Any previous
    token for the same normalized email is replaced and stops working.
    """
    if not verify_password_reset_otp(email, otp):
        return None
    return _issue_reset_token(_normalize_email(email))


def consume_password_reset_token(email, token):
    """
    Consume the reset token for `email`. Returns True exactly once per
    issued token.

    Returns False for: a malformed token (this never touches Redis), an
    unknown, expired or already-used token, a token issued for a different
    email, or a token superseded by a newer one. A failed attempt never
    consumes the valid token.
    """
    if not isinstance(token, str) or _RESET_TOKEN_PATTERN.fullmatch(token) is None:
        return False

    normalized = _normalize_email(email)
    conn = get_redis_connection(_REDIS_ALIAS)
    result = _run_script(
        conn,
        _LUA_CONSUME_RESET_TOKEN,
        [_reset_token_key(normalized)],
        [_reset_token_digest(normalized, token)],
    )
    return int(result) == 1


def peek_password_reset_token(email, token):
    """
    Check the reset token for `email` WITHOUT consuming it (Task P2.4b).
    Returns True when the token is currently valid, False otherwise.

    A malformed token returns False without touching Redis. The stored
    digest is compared with the digest of the supplied token the same way
    consume_password_reset_token() does, but nothing is deleted, so a
    True here is only a hint: the real one-time decision is still made by
    consume_password_reset_token().
    """
    if not isinstance(email, str) or not isinstance(token, str):
        return False
    if _RESET_TOKEN_PATTERN.fullmatch(token) is None:
        return False

    normalized = _normalize_email(email)
    conn = get_redis_connection(_REDIS_ALIAS)
    stored = conn.get(_reset_token_key(normalized))
    if stored is None:
        return False
    supplied = _reset_token_digest(normalized, token)
    return hmac.compare_digest(
        _to_text(stored).encode("ascii"), supplied.encode("ascii")
    )


# ---------------------------------------------------------------------------
# Password reset OTP email delivery (Task P2.3)
#
# Scope: build and send the OTP email only. No API endpoints, serializers,
# views, throttling, password change or reset-token emailing here.
#
# Delivery is synchronous on purpose (no Celery task). A task would have to
# carry the raw OTP through the Celery broker, which is the same Redis, and
# that would put the raw OTP in Redis. The OTP must exist only in the email
# body. See the P2.3 report for the timing side-channel this leaves open.
# ---------------------------------------------------------------------------

_OTP_EMAIL_SUBJECTS = {
    "ar": "رمز إعادة تعيين كلمة المرور",
    "en": "Your password reset code",
}

_OTP_EMAIL_BODIES = {
    "ar": (
        "رمز التحقق الخاص بك لإعادة تعيين كلمة المرور هو: {otp}\n"
        "صالح لمدة {ttl} ثانية فقط.\n"
        "إن لم تطلب ذلك، تجاهل هذه الرسالة."
    ),
    "en": (
        "Your password reset verification code is: {otp}\n"
        "It is valid for {ttl} seconds only.\n"
        "If you did not request this, ignore this email."
    ),
}


def _send_password_reset_otp_email(user, otp):
    """
    Build and send the password-reset OTP email to `user.email`.

    Plain text only (the project has no existing email templates). The
    language follows user.preferred_language ("ar" or "en"); anything else
    falls back to Arabic, the project default. The validity shown in the
    body is derived from OTP_TTL_SECONDS. The subject never contains the
    OTP. The sender is settings.DEFAULT_FROM_EMAIL.

    Lets any send exception propagate; the caller decides what to do.
    """
    language = getattr(user, "preferred_language", None)
    if language not in _OTP_EMAIL_SUBJECTS:
        language = "ar"
    send_mail(
        subject=_OTP_EMAIL_SUBJECTS[language],
        message=_OTP_EMAIL_BODIES[language].format(otp=otp, ttl=OTP_TTL_SECONDS),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


def request_password_reset_otp(email):
    """
    Issue a password-reset OTP for `email` and email it to the account
    owner. Always returns None, whatever happened.

    The caller can not tell these cases apart from the return value or an
    exception: unknown email, inactive account, ambiguous email (more than
    one account), malformed input, successful send, failed send.

    - Lookup is case-insensitive (email__iexact), like authenticate_user().
    - Unknown / inactive / ambiguous: nothing is sent and no Redis key is
      created.
    - Active user: create_password_reset_otp() then email the OTP.
    - Send failure: the OTP just created is invalidated (its Redis keys are
      deleted, using the P2.1 key helpers) and the failure is logged with
      the exception class name only. The log never contains the OTP or the
      email address; exception text is left out because SMTP errors often
      embed the recipient address.

    Known limit: an existing user takes longer than an unknown one (one
    SMTP round trip), which is a timing side-channel. See the P2.3 report.
    """
    if not isinstance(email, str):
        return None
    normalized = _normalize_email(email)
    if not normalized:
        return None

    candidates = list(User.objects.filter(email__iexact=normalized)[:2])
    if len(candidates) != 1:
        return None
    user = candidates[0]
    if not user.is_active:
        return None

    otp = create_password_reset_otp(normalized)
    try:
        _send_password_reset_otp_email(user, otp)
    except Exception as exc:
        conn = get_redis_connection(_REDIS_ALIAS)
        conn.delete(*_otp_keys(normalized))
        logger.error(
            "Password reset OTP email failed (%s); OTP invalidated.",
            type(exc).__name__,
        )
    return None

# ---------------------------------------------------------------------------
# Password reset confirm (Task P2.4)
#
# Scope: set the new password once the reset token has been checked. No
# throttling here (P2.5). The failure signal is a plain False so the caller
# can answer every failure kind identically; a weak password is the one
# exception and is raised as django.core.exceptions.ValidationError so the
# caller can show the password errors.
# ---------------------------------------------------------------------------


def _blacklist_all_outstanding_tokens(user):
    """
    Blacklist every outstanding refresh token of `user` (simplejwt
    token_blacklist). Already-blacklisted tokens are skipped by the unique
    constraint on BlacklistedToken.token (ignore_conflicts).
    """
    BlacklistedToken.objects.bulk_create(
        [
            BlacklistedToken(token=outstanding)
            for outstanding in OutstandingToken.objects.filter(user=user)
        ],
        ignore_conflicts=True,
    )


def confirm_password_reset(email, reset_token, new_password):
    """
    Finish a password reset. Returns True when the password was changed and
    False for every failure that must look the same to the caller: unknown,
    inactive or ambiguous email, or a reset token that is wrong, expired,
    already used or issued for another email.

    Order matters (the password is never judged before the token is known
    to be valid, so a weak password cannot reveal whether an account
    exists):
      1. Find the single active user (email__iexact). No user -> False.
      2. Peek at the reset token without consuming it. Invalid -> False.
      3. Run Django's validate_password against the user. A weak password
         raises ValidationError BEFORE the token is consumed, so the token
         stays valid and the user can retry.
      4. Consume the reset token (atomic, one-time). Failure -> False.
      5. In one transaction: set_password, save, and blacklist all of the
         user's outstanding refresh tokens. Access tokens already issued
         stay valid until they expire.
    """
    if not isinstance(email, str) or not isinstance(new_password, str):
        return False
    normalized = _normalize_email(email)
    if not normalized:
        return False

    candidates = list(
        User.objects.filter(email__iexact=normalized, is_active=True)[:2]
    )
    if len(candidates) != 1:
        return False
    user = candidates[0]

    if not peek_password_reset_token(normalized, reset_token):
        return False

    validate_password(new_password, user=user)

    if not consume_password_reset_token(normalized, reset_token):
        return False

    with transaction.atomic():
        user.set_password(new_password)
        user.save()
        _blacklist_all_outstanding_tokens(user)
    return True
