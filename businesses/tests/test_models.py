import pytest
from django.db import IntegrityError, transaction

from accounts.models import User
from businesses.models import BusinessProfile, CustomerProfile

pytestmark = pytest.mark.django_db


def _make_user(account_type: str, email: str) -> User:
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type=account_type,
    )


class TestBusinessProfileModel:
    def test_business_type_user_gets_exactly_one_profile(self):
        user = _make_user("business", "p024-biz1@example.com")
        profile = BusinessProfile.objects.create(
            user=user,
            business_name="Acme Trading",
            business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
            country="Egypt",
            city="Cairo",
        )
        assert profile.pk is not None
        assert profile.user_id == user.id

    def test_second_profile_for_same_user_fails_at_db_level(self):
        user = _make_user("business", "p024-biz2@example.com")
        BusinessProfile.objects.create(
            user=user,
            business_name="Acme Trading",
            business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
            country="Egypt",
            city="Cairo",
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                BusinessProfile.objects.create(
                    user=user,
                    business_name="Acme Trading 2",
                    business_type=BusinessProfile.BUSINESS_TYPE_FACTORY,
                    country="Egypt",
                    city="Alexandria",
                )

    def test_is_verified_reads_through_to_user_field_no_drift(self):
        user = _make_user("business", "p024-biz3@example.com")
        profile = BusinessProfile.objects.create(
            user=user,
            business_name="Acme Trading",
            business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
            country="Egypt",
            city="Cairo",
        )
        assert profile.is_verified is False

        user.is_business_verified = True
        user.save(update_fields=["is_business_verified"])
        profile.refresh_from_db()
        assert profile.is_verified is True

        user.is_business_verified = False
        user.save(update_fields=["is_business_verified"])
        # profile.user was cached by the previous refresh_from_db() call
        # above — refresh again so this checks a real DB read, not a
        # stale cached related-object reference.
        profile.refresh_from_db()
        assert profile.is_verified is False

    def test_no_separate_stored_is_verified_field_exists(self):
        # Guards against someone accidentally adding a real DB column
        # later and reintroducing the two-sources-of-truth drift.
        field_names = {f.name for f in BusinessProfile._meta.get_fields()}
        assert "is_verified" not in field_names


class TestCustomerProfileModel:
    def test_customer_type_user_gets_exactly_one_profile(self):
        user = _make_user("customer", "p024-cust1@example.com")
        profile = CustomerProfile.objects.create(
            user=user, display_name="John Doe", country="Egypt", city="Ismailia"
        )
        assert profile.pk is not None

    def test_second_profile_for_same_user_fails_at_db_level(self):
        user = _make_user("customer", "p024-cust2@example.com")
        CustomerProfile.objects.create(
            user=user, display_name="John Doe", country="Egypt", city="Ismailia"
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CustomerProfile.objects.create(
                    user=user,
                    display_name="John Doe 2",
                    country="Egypt",
                    city="Cairo",
                )