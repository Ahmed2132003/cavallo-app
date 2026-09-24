"""
Part P-059 — BusinessProfile.is_featured (placeholder) model tests.

The field exists so the Home Feed's backfill tier has a real column to
order by. Real activation/expiry wiring is Phase 15 (P-086/P-087/P-088).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from businesses.models import BusinessProfile
from businesses.services import create_business_profile

User = get_user_model()


class TestBusinessProfileIsFeatured(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="featured-model@example.com",
            email="featured-model@example.com",
            password="testpass123",
            account_type="business",
        )
        self.business = create_business_profile(
            user=self.user,
            business_name="Featured Model Test",
            business_type="trader",
            country="EG",
            city="Ismailia",
        )

    def test_defaults_to_false(self):
        self.assertFalse(self.business.is_featured)

    def test_can_be_set_true_and_persists(self):
        BusinessProfile.objects.filter(pk=self.business.pk).update(is_featured=True)
        self.business.refresh_from_db()
        self.assertTrue(self.business.is_featured)
