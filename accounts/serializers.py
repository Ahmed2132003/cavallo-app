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

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

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
