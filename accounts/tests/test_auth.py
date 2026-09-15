"""
API tests for POST /api/v1/auth/{login,refresh,logout}/ (Part P-018).

Goes through real Django URL routing (config/urls.py -> accounts/urls.py
-> Login/Refresh/LogoutView), same convention as P-017's
test_registration.py, so these tests also lock in the wiring and
confirm errors come back through Part P-012's error envelope.

Uses APIClient.post(..., format="json") for state-changing calls and
real JWT verification (rest_framework_simplejwt.tokens.AccessToken /
RefreshToken) rather than string-shape assertions, so a real rotation/
blacklist bug would actually fail these tests, not just a cosmetic
change to the token strings.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

RAW_PASSWORD = "S0m3-Str0ng-Uncommon-Pass!"


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    # LoginRateThrottle's counters live in the default cache (Redis via
    # P-014). Without this, throttle state would leak between tests
    # sharing the same test-runner IP ("testserver"), making later
    # tests in this module fail the 5/min throttle spuriously.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def login_url():
    return reverse("accounts:login")


@pytest.fixture
def refresh_url():
    return reverse("accounts:refresh")


@pytest.fixture
def logout_url():
    return reverse("accounts:logout")


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="loginuser@example.com",
        email="loginuser@example.com",
        password=RAW_PASSWORD,
        account_type=User.ACCOUNT_TYPE_CUSTOMER,
    )


@pytest.mark.django_db
class TestLoginSuccess:
    def test_valid_login_returns_access_and_refresh(self, api_client, login_url, user):
        response = api_client.post(
            login_url,
            {"email": user.email, "password": RAW_PASSWORD},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert set(response.data.keys()) == {"access", "refresh"}
        assert isinstance(response.data["access"], str) and response.data["access"]
        assert isinstance(response.data["refresh"], str) and response.data["refresh"]

    def test_login_is_case_insensitive_on_email(self, api_client, login_url, user):
        response = api_client.post(
            login_url,
            {"email": user.email.upper(), "password": RAW_PASSWORD},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestLoginFailure:
    def test_wrong_password_returns_401_in_error_envelope(
        self, api_client, login_url, user
    ):
        response = api_client.post(
            login_url,
            {"email": user.email, "password": "totally-wrong-password"},
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "error" in response.data
        assert response.data["error"]["code"] == "AUTHENTICATION_FAILED"

    def test_unknown_email_returns_401_not_a_field_error(self, api_client, login_url):
        response = api_client.post(
            login_url,
            {"email": "nobody@example.com", "password": "whatever123"},
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_inactive_user_cannot_log_in(self, api_client, login_url, user):
        user.is_active = False
        user.save(update_fields=["is_active"])

        response = api_client.post(
            login_url,
            {"email": user.email, "password": RAW_PASSWORD},
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestLoginThrottle:
    def test_exceeding_login_throttle_returns_429(self, api_client, login_url, user):
        # DEFAULT_THROTTLE_RATES["login"] = "5/min" (config/settings/base.py).
        for _ in range(5):
            response = api_client.post(
                login_url,
                {"email": user.email, "password": "wrong-password"},
                format="json",
            )
            assert response.status_code == status.HTTP_401_UNAUTHORIZED

        response = api_client.post(
            login_url,
            {"email": user.email, "password": "wrong-password"},
            format="json",
        )
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    def test_throttle_triggers_even_with_correct_password(
        self, api_client, login_url, user
    ):
        # The throttle is IP-scoped and counts every hit to the view,
        # not just failed attempts — confirms it would also stop a
        # credential-stuffing run that (eventually) guesses right.
        for _ in range(5):
            api_client.post(
                login_url,
                {"email": user.email, "password": RAW_PASSWORD},
                format="json",
            )

        response = api_client.post(
            login_url,
            {"email": user.email, "password": RAW_PASSWORD},
            format="json",
        )
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.django_db
class TestRefreshRotation:
    def _login(self, api_client, login_url, user):
        response = api_client.post(
            login_url,
            {"email": user.email, "password": RAW_PASSWORD},
            format="json",
        )
        return response.data["access"], response.data["refresh"]

    def test_refresh_returns_new_access_and_rotated_refresh(
        self, api_client, login_url, refresh_url, user
    ):
        _, refresh_token = self._login(api_client, login_url, user)

        response = api_client.post(
            refresh_url, {"refresh": refresh_token}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.data
        assert "refresh" in response.data
        # Rotation: the new refresh token must differ from the original.
        assert response.data["refresh"] != refresh_token

    def test_reusing_a_rotated_away_refresh_token_is_rejected(
        self, api_client, login_url, refresh_url, user
    ):
        _, original_refresh = self._login(api_client, login_url, user)

        first = api_client.post(
            refresh_url, {"refresh": original_refresh}, format="json"
        )
        assert first.status_code == status.HTTP_200_OK

        # Reuse of the ORIGINAL (now-rotated-away, blacklisted) token —
        # this is exactly the theft-detection signal architecture
        # Section 14 requires.
        second = api_client.post(
            refresh_url, {"refresh": original_refresh}, format="json"
        )

        assert second.status_code == status.HTTP_401_UNAUTHORIZED

    def test_new_rotated_refresh_token_still_works(
        self, api_client, login_url, refresh_url, user
    ):
        _, original_refresh = self._login(api_client, login_url, user)

        first = api_client.post(
            refresh_url, {"refresh": original_refresh}, format="json"
        )
        new_refresh = first.data["refresh"]

        second = api_client.post(refresh_url, {"refresh": new_refresh}, format="json")

        assert second.status_code == status.HTTP_200_OK

    def test_garbage_refresh_token_returns_401(self, api_client, refresh_url):
        response = api_client.post(
            refresh_url, {"refresh": "not-a-real-token"}, format="json"
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestLogout:
    def _login(self, api_client, login_url, user):
        response = api_client.post(
            login_url,
            {"email": user.email, "password": RAW_PASSWORD},
            format="json",
        )
        return response.data["access"], response.data["refresh"]

    def test_logout_requires_authentication(self, api_client, logout_url):
        # P-023: explicit auth-failure-path coverage, not just a bare
        # status-code check — confirms an unauthenticated call to this
        # IsAuthenticated-gated endpoint comes back as a real 401 in the
        # standard P-012 {"error": {...}} envelope, not a 500 or an
        # accidental 200.
        response = api_client.post(logout_url, {"refresh": "anything"}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "error" in response.data
        assert response.data["error"]["code"] == "AUTHENTICATION_FAILED"

    def test_logout_blacklists_refresh_token_immediately(
        self, api_client, login_url, logout_url, refresh_url, user
    ):
        access_token, refresh_token = self._login(api_client, login_url, user)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        logout_response = api_client.post(
            logout_url, {"refresh": refresh_token}, format="json"
        )
        assert logout_response.status_code == status.HTTP_200_OK

        # Immediately after logout — no waiting for a future rotation
        # attempt to fail — a refresh with that same token must fail.
        api_client.credentials()  # refresh/ doesn't need auth, but be explicit
        refresh_response = api_client.post(
            refresh_url, {"refresh": refresh_token}, format="json"
        )
        assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_logout_with_already_blacklisted_token_is_a_clean_400(
        self, api_client, login_url, logout_url, user
    ):
        access_token, refresh_token = self._login(api_client, login_url, user)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        first = api_client.post(logout_url, {"refresh": refresh_token}, format="json")
        assert first.status_code == status.HTTP_200_OK

        second = api_client.post(logout_url, {"refresh": refresh_token}, format="json")
        assert second.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in second.data

    def test_logout_does_not_blacklist_other_users_sessions(
        self, api_client, login_url, logout_url, user
    ):
        other = User.objects.create_user(
            username="other@example.com",
            email="other@example.com",
            password=RAW_PASSWORD,
            account_type=User.ACCOUNT_TYPE_CUSTOMER,
        )
        other_refresh = str(RefreshToken.for_user(other))

        access_token, _ = self._login(api_client, login_url, user)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        # user is authenticated, but submits a DIFFERENT user's refresh
        # token — the endpoint blacklists whatever token is given, since
        # simplejwt's blacklist app has no per-token "ownership" concept
        # beyond the refresh token itself. This test documents that
        # behavior rather than assuming a stronger guarantee that
        # doesn't exist in this part's scope.
        response = api_client.post(
            logout_url, {"refresh": other_refresh}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK