"""
Tests for accounts.models.User (Part P-016).

Exercises the acceptance criteria directly against a real test-database
row (pytest.mark.django_db), not mocks: a Customer and a Business user
can both be created with the correct account_type, the new role fields
default correctly, and account_type's "default sensibly rather than
require an explicit value" decision (see the model's own docstring)
actually holds for both plain user creation and createsuperuser.
"""

import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.mark.django_db
class TestUserAccountType:
    def test_customer_user_is_creatable_with_correct_account_type(self):
        user = User.objects.create_user(
            username="customer1",
            password="pw12345",
            account_type=User.ACCOUNT_TYPE_CUSTOMER,
        )

        assert user.account_type == "customer"

    def test_business_user_is_creatable_with_correct_account_type(self):
        user = User.objects.create_user(
            username="business1",
            password="pw12345",
            account_type=User.ACCOUNT_TYPE_BUSINESS,
        )

        assert user.account_type == "business"

    def test_account_type_defaults_to_customer_when_not_supplied(self):
        # Locks in this part's documented decision: account_type is never
        # blank, but callers (including manage.py createsuperuser, which
        # has no prompt for this field) are never forced to supply it.
        user = User.objects.create_user(username="nodefault", password="pw12345")

        assert user.account_type == "customer"


@pytest.mark.django_db
class TestUserRoleFieldDefaults:
    def test_is_moderator_defaults_to_false(self):
        user = User.objects.create_user(username="mod-default", password="pw12345")

        assert user.is_moderator is False

    def test_is_business_verified_defaults_to_false(self):
        user = User.objects.create_user(username="verify-default", password="pw12345")

        assert user.is_business_verified is False

    def test_is_moderator_can_be_set_true(self):
        user = User.objects.create_user(
            username="mod1", password="pw12345", is_moderator=True
        )

        assert user.is_moderator is True

    def test_is_business_verified_can_be_set_true_for_business_account(self):
        user = User.objects.create_user(
            username="verified-biz",
            password="pw12345",
            account_type=User.ACCOUNT_TYPE_BUSINESS,
            is_business_verified=True,
        )

        assert user.account_type == "business"
        assert user.is_business_verified is True


@pytest.mark.django_db
class TestSuperuserFieldMapping:
    def test_createsuperuser_sets_is_superuser_and_is_staff(self):
        superuser = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw12345"
        )

        assert superuser.is_superuser is True
        assert superuser.is_staff is True

    def test_createsuperuser_still_gets_a_valid_account_type(self):
        # Super Admin's capabilities come from is_superuser/is_staff, not
        # account_type — but the field is never null/blank, so it still
        # needs *some* value. Confirms the documented default holds here
        # too, since createsuperuser has no prompt for this field.
        superuser = User.objects.create_superuser(
            username="root2", email="root2@example.com", password="pw12345"
        )

        assert superuser.account_type == "customer"


@pytest.mark.django_db
class TestUserTimestamps:
    def test_created_at_and_updated_at_are_set_on_create(self):
        user = User.objects.create_user(username="ts-user", password="pw12345")

        assert user.created_at is not None
        assert user.updated_at is not None

    def test_updated_at_changes_on_save(self):
        user = User.objects.create_user(username="ts-user2", password="pw12345")
        original_updated_at = user.updated_at

        user.is_moderator = True
        user.save()
        user.refresh_from_db()

        assert user.updated_at > original_updated_at
