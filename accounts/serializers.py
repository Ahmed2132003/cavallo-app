"""
Serializers for the accounts app's authentication API (Part P-017).

RegisterSerializer is the entire validation surface for
POST /api/v1/auth/register/ (accounts/views.py). Kept deliberately
minimal per this part's own scope decision (see accounts/services.py's
module docstring and PROJECT_PROGRESS.md's P-017 entry): email,
password, password_confirm, account_type only. Trader/Factory
business_type and phone number are NOT collected here — they're
deferred to Phase 4's "complete your business profile" step (Part
P-042), which is why this serializer has no business_type field at all,
for either account type.
"""

import uuid

import magic
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed

from accounts.services import authenticate_user
from core.media import validate_upload

User = get_user_model()


class RegisterSerializer(serializers.Serializer):
    """
    Validates a Customer or Business registration request.

    Deliberately a plain Serializer (not a ModelSerializer): the model
    (accounts.models.User) still requires `username` since Part P-016
    didn't override USERNAME_FIELD, but this part's registration
    surface is intentionally email/password/account_type only (see the
    module docstring above) — a ModelSerializer would either need to
    expose `username` as a field (out of scope) or fight Meta.fields
    exclusions for no real benefit. accounts.services.register_user()
    is what derives `username` from the validated email.
    """

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    password_confirm = serializers.CharField(
        write_only=True, style={"input_type": "password"}
    )
    account_type = serializers.ChoiceField(choices=User.ACCOUNT_TYPE_CHOICES)

    def validate_email(self, value):
        # Case-insensitive per this part's acceptance criteria — two
        # signups differing only by email case are the same account for
        # this platform. Note: accounts.models.User does not itself have
        # a DB-level unique/case-insensitive constraint on email (Part
        # P-016 left AbstractUser's `email` field untouched, and this
        # part's own scope explicitly excludes modifying the User model
        # — see PROJECT_PROGRESS.md's P-017 entry). This check is
        # therefore an application-level guard, not a DB constraint;
        # flagged there as a follow-up for a future part to consider.
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_password(self, value):
        # Runs every validator in AUTH_PASSWORD_VALIDATORS
        # (config/settings/base.py) — UserAttributeSimilarityValidator,
        # MinimumLengthValidator, CommonPasswordValidator,
        # NumericPasswordValidator. Django's validate_password raises
        # its own ValidationError (django.core.exceptions), which DRF
        # doesn't understand as a field error on its own — re-raised
        # here as rest_framework.exceptions.ValidationError so it comes
        # back attached to the "password" field, in the standard
        # envelope shape (core/exceptions.py).
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def validate(self, attrs):
        if attrs.get("password") != attrs.get("password_confirm"):
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match."}
            )
        return attrs


class LoginSerializer(serializers.Serializer):
    """
    Validates POST /api/v1/auth/login/ (Part P-018).

    Public field is `email`, not `username` — even though
    accounts.models.User (P-016) never overrode USERNAME_FIELD, so
    Django's real auth machinery still authenticates on `username`
    under the hood. accounts.services.authenticate_user() bridges the
    two: it looks the user up by case-insensitive email (mirroring
    RegisterSerializer.validate_email's own case-insensitive check
    above) and then calls Django's authenticate() with that user's
    real `username` (== the exact email string stored at registration
    time by accounts.services.register_user()). Callers of this
    serializer never need to know `username` exists at all.

    Deliberately does NOT reveal whether the failure was "no such
    email" vs "wrong password" — both collapse into the same generic
    401, which is standard practice against user-enumeration.
    """

    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )

    def validate(self, attrs):
        user = authenticate_user(email=attrs["email"], password=attrs["password"])
        if user is None:
            raise AuthenticationFailed(
                "Unable to log in with the provided credentials."
            )
        if not user.is_active:
            raise AuthenticationFailed("This account is inactive.")
        attrs["user"] = user
        return attrs


class LogoutSerializer(serializers.Serializer):
    """
    Validates POST /api/v1/auth/logout/ (Part P-018).

    Just the refresh token to blacklist — accounts.views.LogoutView
    does the actual blacklisting via accounts.services.
    blacklist_refresh_token(), converting a simplejwt TokenError into
    the standard validation-error envelope (Part P-012) rather than
    letting it surface as an unhandled 500.
    """

    refresh = serializers.CharField()


class MePreferencesSerializer(serializers.Serializer):
    """
    Validates PATCH /api/v1/auth/me/ (Part P-112).

    Only the fields a user may change about themselves live here. Role
    flags such as ``is_staff`` or ``account_type`` are deliberately not
    declared, so a forged value in the request body is ignored.
    """

    preferred_language = serializers.ChoiceField(
        choices=[value for value, _label in User.LANGUAGE_CHOICES],
        required=False,
    )


# Part P-116A-1: real (sniffed) MIME type -> the only extension the stored
# object may carry. The client-supplied filename is never kept.
AVATAR_ALLOWED_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
AVATAR_MAX_BYTES = 5 * 1024 * 1024
DISPLAY_NAME_MAX_LENGTH = 80


class MeProfileUpdateSerializer(serializers.Serializer):
    """
    Validates PATCH /api/v1/auth/me/profile/ (Part P-116A-1).

    Only display_name, avatar and remove_avatar are declared, so any other
    key in the body (is_staff, is_moderator, account_type, email,
    preferred_language, id, ...) is silently dropped by DRF. Use with
    ``partial=True``.
    """

    display_name = serializers.CharField(
        max_length=DISPLAY_NAME_MAX_LENGTH, allow_blank=True, required=False
    )
    avatar = serializers.FileField(required=False)
    remove_avatar = serializers.BooleanField(required=False)

    def validate_avatar(self, value):
        # Size + real content type (libmagic), not filename/Content-Type.
        # Raises Django's ValidationError, which DRF converts into a
        # field error (see core/media.py's integration note).
        validate_upload(value, list(AVATAR_ALLOWED_TYPES), AVATAR_MAX_BYTES)

        # Store under a server-generated name with the extension that
        # matches the real type, so e.g. a PNG uploaded as "x.html" is
        # never stored/served as HTML.
        head = value.read(2048)
        value.seek(0)
        extension = AVATAR_ALLOWED_TYPES[magic.from_buffer(head, mime=True)]
        value.name = f"{uuid.uuid4().hex}.{extension}"
        return value

    def validate(self, attrs):
        if "avatar" in attrs and attrs.get("remove_avatar"):
            raise serializers.ValidationError(
                {"remove_avatar": "Cannot be combined with avatar."}
            )
        has_change = (
            "display_name" in attrs or "avatar" in attrs or attrs.get("remove_avatar")
        )
        if not has_change:
            raise serializers.ValidationError(
                "Provide at least one of: display_name, avatar, remove_avatar."
            )
        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    """
    Input for POST /password-reset/request/ (Task P2.4).

    Format check only. Deliberately no database lookup: the view must do
    the same work for every well-formed email.
    """

    email = serializers.EmailField()


class PasswordResetVerifySerializer(serializers.Serializer):
    """
    Input for POST /password-reset/verify/ (Task P2.4).

    The OTP is not pattern-checked here: a malformed OTP must produce the
    same unified failure as a wrong one (services handles it).
    """

    email = serializers.EmailField()
    otp = serializers.CharField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    """
    Input for POST /password-reset/confirm/ (Task P2.4).

    Password strength is checked in services.confirm_password_reset(), not
    here, because it must run after the user lookup and before the token is
    consumed.
    """

    email = serializers.EmailField()
    reset_token = serializers.CharField()
    new_password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )
