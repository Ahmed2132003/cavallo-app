"""
Part P-024 service layer. Per Section 8's service-layer rule, profile
creation must happen through these functions — never directly in a
view/serializer .save() call. Both are wrapped in transaction.atomic()
so a partially-created profile can never persist if anything inside
the block fails.

These raise django.core.exceptions.ValidationError, matching the
convention P-013's own handoff note establishes for this codebase:
whatever future endpoint (P-026's CRUD serializer) calls into this
service layer should catch/re-raise from inside a serializer's
validate() method, which is what lets DRF turn it into P-012's
{"error": {...}} envelope automatically. This service layer itself has
no DRF dependency, on purpose — it must stay callable from plain
Python, Django Admin actions, and management commands alike.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import BusinessProfile, CustomerProfile

ACCOUNT_TYPE_BUSINESS = "business"
ACCOUNT_TYPE_CUSTOMER = "customer"


def create_business_profile(
    user,
    business_name: str,
    business_type: str,
    country: str,
    city: str,
    description: str = "",
) -> BusinessProfile:
    if getattr(user, "account_type", None) != ACCOUNT_TYPE_BUSINESS:
        raise ValidationError(
            "Only a Business-type user can have a BusinessProfile created."
        )
    if BusinessProfile.all_objects.filter(user=user).exists():
        raise ValidationError("This user already has a BusinessProfile.")

    with transaction.atomic():
        return BusinessProfile.objects.create(
            user=user,
            business_name=business_name,
            business_type=business_type,
            country=country,
            city=city,
            description=description,
        )


def create_customer_profile(
    user,
    display_name: str,
    country: str,
    city: str,
) -> CustomerProfile:
    if getattr(user, "account_type", None) != ACCOUNT_TYPE_CUSTOMER:
        raise ValidationError(
            "Only a Customer-type user can have a CustomerProfile created."
        )
    if CustomerProfile.all_objects.filter(user=user).exists():
        raise ValidationError("This user already has a CustomerProfile.")

    with transaction.atomic():
        return CustomerProfile.objects.create(
            user=user,
            display_name=display_name,
            country=country,
            city=city,
        )