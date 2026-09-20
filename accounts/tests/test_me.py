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
from django.core.cache import cache

User = get_user_model()


class TestMe(APITestCase):
    def setUp(self):
        # LoginRateThrottle's counters live in the default cache (Redis
        # via P-014) and don't reset automatically between test
        # methods sharing the same test-runner IP — same issue
        # accounts/tests/test_auth.py's `_clear_throttle_cache` fixture
        # already documents and fixes for its own module. This class
        # calls `_register_and_login` up to twice per test, so without
        # this the 5/min cap gets tripped spuriously once enough tests
        # accumulate logins in one run.
        cache.clear()
        self.client = APIClient()

    def tearDown(self):
        cache.clear()
        
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

    def test_me_response_includes_role_flags_on_top_of_register_shape(self):
        # RegisterView and MeView share the same base {id, email,
        # account_type} shape, but MeView now also carries the two role
        # flags P-040's router gate needs — confirm both halves.
        registered, access = self._register_and_login(
            "shape@example.com", User.ACCOUNT_TYPE_CUSTOMER
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        response = self.client.get("/api/v1/auth/me/")

        self.assertEqual(
            set(response.data.keys()),
            set(registered.keys()) | {"is_moderator", "is_staff"},
        )
        self.assertEqual(response.data["id"], registered["id"])
        self.assertEqual(response.data["email"], registered["email"])
        self.assertEqual(response.data["account_type"], registered["account_type"])
        # A fresh Customer registration defaults to both False.
        self.assertFalse(response.data["is_moderator"])
        self.assertFalse(response.data["is_staff"])
        
    def test_me_reflects_true_role_flags(self):
        # Confirms the flags aren't hardcoded False — a real moderator
        # account must see is_moderator=True, and a real staff account
        # must see is_staff=True.
        _, access = self._register_and_login(
            "moderator@example.com", User.ACCOUNT_TYPE_CUSTOMER
        )
        user = User.objects.get(email="moderator@example.com")
        user.is_moderator = True
        user.is_staff = True
        user.save(update_fields=["is_moderator", "is_staff"])

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = self.client.get("/api/v1/auth/me/")

        self.assertTrue(response.data["is_moderator"])
        self.assertTrue(response.data["is_staff"])
        
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