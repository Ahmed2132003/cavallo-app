"""
Authentication API views for the accounts app (Parts P-017, P-018).
"""

from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.views import TokenRefreshView

from accounts import services
from accounts.serializers import LoginSerializer, LogoutSerializer, RegisterSerializer
from accounts.throttles import LoginRateThrottle


class RegisterView(generics.CreateAPIView):
    """
    POST /api/v1/auth/register/

    Registers a Customer or Business account in one endpoint,
    discriminated by `account_type`. Section 8: no business logic here
    — validation lives in RegisterSerializer, account creation lives in
    accounts.services.register_user(); this view only wires the two
    together and shapes the response.

    Deliberately does NOT issue JWT tokens or log the user in: login is
    a separate endpoint (Part P-018). Flagged here rather than decided
    silently — see PROJECT_PROGRESS.md's P-017 entry if this should be
    reconsidered later.
    """

    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = services.register_user(
            email=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
            account_type=serializer.validated_data["account_type"],
        )

        # Minimal representation only — never the password (hashed or
        # otherwise), and no BusinessProfile fields since none exist
        # yet for a business account at this point in the flow (see
        # accounts/services.py's module docstring).
        return Response(
            {
                "id": user.id,
                "email": user.email,
                "account_type": user.account_type,
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(generics.GenericAPIView):
    """
    POST /api/v1/auth/login/

    Architecture's #2 named security threat mitigation (Broken Auth via
    JWT theft) starts here: credential validation (email/password) is
    the serializer's job (LoginSerializer -> accounts.services.
    authenticate_user()); this view only turns a validated `user` into
    a token pair (accounts.services.issue_token_pair()) and shapes the
    response. Section 8's "no business logic in views" rule applies
    here exactly as it did in P-017's RegisterView.

    LoginRateThrottle (accounts/throttles.py) is attached ONLY to this
    view — see that module's docstring and REST_FRAMEWORK["DEFAULT_
    THROTTLE_RATES"]["login"] in config/settings/base.py for why this
    is scoped, not global.
    """

    serializer_class = LoginSerializer
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        tokens = services.issue_token_pair(user)

        return Response(tokens, status=status.HTTP_200_OK)


class RefreshView(TokenRefreshView):
    """
    POST /api/v1/auth/refresh/

    Thin wrapper around simplejwt's own TokenRefreshView — no override
    needed. Rotation + reuse-detection behavior comes entirely from
    SIMPLE_JWT's ROTATE_REFRESH_TOKENS/BLACKLIST_AFTER_ROTATION
    (config/settings/base.py, Part P-018): every call here rotates the
    presented refresh token (issues a new one, blacklists the old one),
    and re-presenting an already-rotated-away token is rejected because
    it's already blacklisted — exactly the theft-detection behavior
    architecture Section 14 requires.

    Kept as a named subclass (rather than wiring TokenRefreshView
    directly in urls.py) purely so this endpoint has a stable,
    documented name in this file that P-022 (Flutter's silent-refresh
    interceptor) can be pointed at from this docstring, and so a future
    part can override behavior here without touching urls.py.
    """

    pass


class LogoutView(generics.GenericAPIView):
    """
    POST /api/v1/auth/logout/

    Requires the caller to be authenticated (a valid, non-expired
    access token) — deliberately IsAuthenticated, not AllowAny, so an
    arbitrary refresh token alone can't be used to blacklist someone
    else's session without also presenting a currently-valid access
    token for that same session.

    Blacklists the given refresh token immediately via
    accounts.services.blacklist_refresh_token(), on top of (not instead
    of) the rotation-driven blacklisting rotation already provides —
    see that function's docstring for why the explicit call still
    matters. A malformed/expired/already-blacklisted token is
    translated from simplejwt's TokenError into a standard DRF
    ValidationError so the response still comes back in Part P-012's
    {"error": {...}} envelope shape instead of an unhandled 500.
    """

    serializer_class = LogoutSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            services.blacklist_refresh_token(serializer.validated_data["refresh"])
        except TokenError as exc:
            raise ValidationError({"refresh": str(exc)})

        return Response(
            {"detail": "Successfully logged out."}, status=status.HTTP_200_OK
        )
