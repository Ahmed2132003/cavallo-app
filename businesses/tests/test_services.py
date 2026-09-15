import pytest
from django.core.exceptions import ValidationError

from accounts.models import User
from businesses.models import BusinessProfile, CustomerProfile
from businesses.services import create_business_profile, create_customer_profile

pytestmark = pytest.mark.django_db


def _make_user(account_type: str, email: str) -> User:
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type=account_type,
    )


class TestCreateBusinessProfile:
    def test_creates_profile_for_business_type_user(self):
        user = _make_user("business", "p024-svc-biz1@example.com")
        profile = create_business_profile(
            user=user,
            business_name="Acme Trading",
            business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
            country="Egypt",
            city="Cairo",
        )
        assert isinstance(profile, BusinessProfile)
        assert BusinessProfile.objects.filter(user=user).count() == 1

    def test_rejects_customer_type_user_and_creates_no_row(self):
        user = _make_user("customer", "p024-svc-biz2@example.com")
        with pytest.raises(ValidationError):
            create_business_profile(
                user=user,
                business_name="Acme Trading",
                business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
                country="Egypt",
                city="Cairo",
            )
        assert BusinessProfile.objects.filter(user=user).count() == 0

    def test_rejects_second_profile_for_same_business_user(self):
        user = _make_user("business", "p024-svc-biz3@example.com")
        create_business_profile(
            user=user,
            business_name="Acme Trading",
            business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
            country="Egypt",
            city="Cairo",
        )
        with pytest.raises(ValidationError):
            create_business_profile(
                user=user,
                business_name="Acme Trading 2",
                business_type=BusinessProfile.BUSINESS_TYPE_FACTORY,
                country="Egypt",
                city="Alexandria",
            )
        assert BusinessProfile.objects.filter(user=user).count() == 1


class TestCreateCustomerProfile:
    def test_creates_profile_for_customer_type_user(self):
        user = _make_user("customer", "p024-svc-cust1@example.com")
        profile = create_customer_profile(
            user=user, display_name="John Doe", country="Egypt", city="Ismailia"
        )
        assert isinstance(profile, CustomerProfile)
        assert CustomerProfile.objects.filter(user=user).count() == 1

    def test_rejects_business_type_user_and_creates_no_row(self):
        user = _make_user("business", "p024-svc-cust2@example.com")
        with pytest.raises(ValidationError):
            create_customer_profile(
                user=user, display_name="John Doe", country="Egypt", city="Ismailia"
            )
        assert CustomerProfile.objects.filter(user=user).count() == 0

    def test_rejects_second_profile_for_same_customer_user(self):
        user = _make_user("customer", "p024-svc-cust3@example.com")
        create_customer_profile(
            user=user, display_name="John Doe", country="Egypt", city="Ismailia"
        )
        with pytest.raises(ValidationError):
            create_customer_profile(
                user=user, display_name="John Doe 2", country="Egypt", city="Cairo"
            )
        assert CustomerProfile.objects.filter(user=user).count() == 1
