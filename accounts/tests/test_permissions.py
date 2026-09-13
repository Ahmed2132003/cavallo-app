"""
Tests for `core.permissions.HasCapability` and the seeded Moderator /
Admin / SuperAdmin Groups (Part P-019).

Hits a real throwaway view (accounts/tests/views.py) through the full
DRF view-dispatch path via accounts/tests/urls.py, so this exercises
HasCapability as an actual `permission_classes` entry — not by calling
it directly — and confirms the resulting 403 comes back in the
standard P-012 error envelope.

Group membership is exercised directly against real database rows
(pytest.mark.django_db), which in turn depend on the accounts.0003
data migration having already seeded the three Groups with their
permission sets — these tests fetch those Groups by name rather than
recreating them, so a failure here also catches drift between
accounts/models.py's Meta.permissions and the 0003 migration's
GROUP_PERMS mapping.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APIClient

User = get_user_model()

pytestmark = [pytest.mark.django_db, pytest.mark.urls("accounts.tests.urls")]


@pytest.fixture
def client():
    return APIClient()


def _make_user(username, **extra):
    return User.objects.create_user(
        username=username,
        password="pw12345",
        account_type=User.ACCOUNT_TYPE_CUSTOMER,
        **extra,
    )


class TestGroupsSeededCorrectly:
    """
    Confirms the accounts.0003 data migration actually ran and produced
    the exact Group -> permission mapping this framework depends on.
    """

    def test_moderator_group_has_only_moderation_permission(self):
        group = Group.objects.get(name="Moderator")
        codenames = set(group.permissions.values_list("codename", flat=True))
        assert codenames == {"can_moderate_content"}

    def test_admin_group_has_full_admin_permission_set(self):
        group = Group.objects.get(name="Admin")
        codenames = set(group.permissions.values_list("codename", flat=True))
        assert codenames == {
            "can_moderate_content",
            "can_manage_categories",
            "can_ban_users",
            "can_manage_notifications",
            "can_manage_monetization",
        }

    def test_superadmin_group_matches_admin_permission_set(self):
        admin_codenames = set(
            Group.objects.get(name="Admin").permissions.values_list(
                "codename", flat=True
            )
        )
        superadmin_codenames = set(
            Group.objects.get(name="SuperAdmin").permissions.values_list(
                "codename", flat=True
            )
        )
        assert superadmin_codenames == admin_codenames


class TestHasCapabilityAgainstRealEndpoint:
    """
    Exercises HasCapability("can_moderate_content") through a real HTTP
    request against the throwaway test view/urlconf.
    """

    def test_moderator_group_member_can_access(self, client):
        user = _make_user("moderator_user")
        user.groups.add(Group.objects.get(name="Moderator"))

        client.force_authenticate(user=user)
        response = client.get("/moderation-test/")

        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_admin_group_member_can_access(self, client):
        user = _make_user("admin_user")
        user.groups.add(Group.objects.get(name="Admin"))

        client.force_authenticate(user=user)
        response = client.get("/moderation-test/")

        assert response.status_code == 200

    def test_superuser_can_access_without_any_group(self, client):
        # is_superuser implicitly satisfies has_perm() for every
        # permission (Django's own auth backend behavior) — confirms
        # HasCapability doesn't need a special-case for this, it falls
        # naturally out of has_perm().
        user = User.objects.create_superuser(
            username="superuser1", password="pw12345", email="su@example.com"
        )

        client.force_authenticate(user=user)
        response = client.get("/moderation-test/")

        assert response.status_code == 200

    def test_plain_customer_cannot_access(self, client):
        user = _make_user("plain_customer")

        client.force_authenticate(user=user)
        response = client.get("/moderation-test/")

        assert response.status_code == 403
        body = response.json()
        assert body["error"]["code"] == "PERMISSION_DENIED"

    def test_unauthenticated_request_cannot_access(self, client):
        response = client.get("/moderation-test/")

        assert response.status_code in (401, 403)

    def test_removing_user_from_group_immediately_revokes_access(self, client):
        """
        Real finding during this part's own validation (not assumed):
        Django's `User.has_perm()` caches its result on the Python
        *instance* (`_perm_cache`/`_group_perm_cache`) for that
        instance's lifetime — confirmed directly in a shell: calling
        `has_perm()` again on the SAME already-queried object after
        `user.groups.remove(...)` still returned `True`, while a
        freshly-`User.objects.get(...)`-fetched object correctly
        returned `False`.

        This is not a bug in HasCapability or in real request handling:
        every real HTTP request re-authenticates via JWT
        (`JWTAuthentication`), which fetches a brand-new `User` row
        from the database on every single request — so a stale cache
        never has a chance to exist in production; access is revoked
        on the very next request with zero code change and no token
        refresh needed, exactly as the acceptance criteria require.

        The only reason a naive version of this test could show a false
        "still has access after removal" result is that DRF's own
        `force_authenticate()` test helper reuses the exact same
        in-memory user object across every request made by this
        `client` unless re-called — which a real client over HTTP never
        does. Re-fetching the user from the DB and re-authenticating
        before the second request correctly simulates a real second
        HTTP request (fresh row, fresh has_perm() cache), which is what
        this test asserts on.
        """
        user = _make_user("temp_moderator")
        moderator_group = Group.objects.get(name="Moderator")
        user.groups.add(moderator_group)

        client.force_authenticate(user=user)
        response = client.get("/moderation-test/")
        assert response.status_code == 200

        # Remove from the group — no code change, no re-login, no token
        # refresh: the very next real HTTP request must be denied.
        user.groups.remove(moderator_group)

        # Re-fetch + re-authenticate to simulate what a real second HTTP
        # request actually does (JWTAuthentication fetches a fresh User
        # row every time) — see the docstring above for why this isn't
        # papering over a real bug.
        fresh_user = User.objects.get(pk=user.pk)
        client.force_authenticate(user=fresh_user)

        response = client.get("/moderation-test/")
        assert response.status_code == 403

    def test_business_group_membership_does_not_leak_moderator_access(self, client):
        # An Admin-group member should NOT gain access via some other
        # unrelated group — only via a group that actually carries
        # can_moderate_content (or is_superuser).
        other_group, _ = Group.objects.get_or_create(name="SomeUnrelatedGroup")
        user = _make_user("unrelated_group_user")
        user.groups.add(other_group)

        client.force_authenticate(user=user)
        response = client.get("/moderation-test/")

        assert response.status_code == 403
