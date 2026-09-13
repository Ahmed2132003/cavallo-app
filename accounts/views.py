"""
Authentication API views for the accounts app (Part P-017).
"""

from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts import services
from accounts.serializers import RegisterSerializer


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
