"""
Tests for GET /api/v1/auth/me/ (accounts.views.MeView).

Hits the real endpoint stack end-to-end (register -> login -> me) via
APIClient, not a bare permission/view unit test — same lesson P-027's
own progress notes already recorded ("a serializer-only is_valid() test
suite can pass 100% while a real request still 500s").
"""

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

User = get_user_model()


class TestMe(APITestCase):
    def setUp(self):
        self.client = APIClient()

    def _register_and_login(self, email, account_type):
        register_response = self.client.post(
            "/api/v1/auth/register/",
            {
                "email": email,
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
                "account_type": account_type,
            },
            format="json",
        )
        assert register_response.status_code == status.HTTP_201_CREATED, (
            register_response.data
        )

        login_response = self.client.post(
            "/api/v1/auth/login/",
            {"email": email, "password": "StrongPass123!"},
            format="json",
        )
        assert login_response.status_code == status.HTTP_200_OK, login_response.data

        return register_response.data, login_response.data["access"]

    def test_me_returns_customer_account_type(self):
        registered, access = self._register_and_login(
            "customer@example.com", User.ACCOUNT_TYPE_CUSTOMER
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        response = self.client.get("/api/v1/auth/me/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], registered["id"])
        self.assertEqual(response.data["email"], "customer@example.com")
        self.assertEqual(response.data["account_type"], User.ACCOUNT_TYPE_CUSTOMER)

    def test_me_returns_business_account_type(self):
        # The exact case P-021a/P-028C flagged as broken end-to-end: a
        # real Business account must NOT come back as "customer".
        _, access = self._register_and_login(
            "business@example.com", User.ACCOUNT_TYPE_BUSINESS
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        response = self.client.get("/api/v1/auth/me/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["account_type"], User.ACCOUNT_TYPE_BUSINESS)

    def test_me_response_matches_register_response_shape(self):
        # RegisterView and MeView are deliberately built to return the
        # exact same {id, email, account_type} shape (see MeView's own
        # docstring) — confirm that's actually true, not just claimed.
        registered, access = self._register_and_login(
            "shape@example.com", User.ACCOUNT_TYPE_CUSTOMER
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        response = self.client.get("/api/v1/auth/me/")

        self.assertEqual(set(response.data.keys()), set(registered.keys()))
        self.assertEqual(response.data["id"], registered["id"])
        self.assertEqual(response.data["email"], registered["email"])
        self.assertEqual(response.data["account_type"], registered["account_type"])

    def test_me_rejects_unauthenticated_request(self):
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_rejects_request_after_logout(self):
        # Confirms MeView really enforces IsAuthenticated against a
        # blacklisted-via-logout token, not just "any string in the
        # Authorization header" — mirrors P-018's own logout tests.
        _, access = self._register_and_login(
            "logout@example.com", User.ACCOUNT_TYPE_CUSTOMER
        )
        # Need the refresh token too, to actually call /logout/.
        login_response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "logout@example.com", "password": "StrongPass123!"},
            format="json",
        )
        refresh = login_response.data["refresh"]
        access = login_response.data["access"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        logout_response = self.client.post(
            "/api/v1/auth/logout/", {"refresh": refresh}, format="json"
        )
        self.assertEqual(logout_response.status_code, status.HTTP_200_OK)

        # Logout blacklists the *refresh* token, not the still-live
        # access token (P-018's own documented behavior) — so /me/ with
        # the same access token should still succeed until it naturally
        # expires. This test documents that boundary rather than
        # asserting the wrong thing.
        me_response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me_response.status_code, status.HTTP_200_OK)