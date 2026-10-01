"""Django Admin tests for the monetization app (Part P-086)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.urls import reverse

from businesses.services import create_business_profile
from monetization.models import FeaturedSubscription, Plan
from monetization.services import activate_subscription

User = get_user_model()

ADD_URL = "admin:monetization_featuredsubscription_add"
CHANGELIST_URL = "admin:monetization_featuredsubscription_changelist"


def _make_business(suffix="a"):
    email = f"monetization-admin-{suffix}@example.com"
    user = User.objects.create_user(
        username=email,
        email=email,
        password="testpass123",
        account_type="business",
    )
    return create_business_profile(
        user=user,
        business_name=f"Monetization Admin Trader {suffix}",
        business_type="trader",
        country="EG",
        city="Ismailia",
    )


def _make_plan(name="Featured 30", days=30):
    return Plan.objects.create(
        name=name,
        duration_days=days,
        price=Decimal("250.00"),
        currency="EGP",
    )


def _active(business):
    return FeaturedSubscription.objects.filter(business=business, is_active=True)


@pytest.mark.django_db
class TestMonetizationAdmin:
    def test_models_are_registered(self):
        assert admin.site.is_registered(Plan)
        assert admin.site.is_registered(FeaturedSubscription)

    def test_changelists_load(self, admin_client):
        activate_subscription(_make_business(), _make_plan())

        plans = admin_client.get(reverse("admin:monetization_plan_changelist"))
        subs = admin_client.get(reverse(CHANGELIST_URL))

        assert plans.status_code == 200
        assert subs.status_code == 200

    def test_add_form_only_offers_business_and_plan(self, admin_client):
        response = admin_client.get(reverse(ADD_URL))

        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="business"' in content
        assert 'name="plan"' in content
        assert 'name="is_active"' not in content
        assert 'name="starts_at' not in content
        assert 'name="expires_at' not in content

    def test_add_post_activates_through_the_service(self, admin_client):
        business = _make_business()
        plan = _make_plan(days=30)

        response = admin_client.post(
            reverse(ADD_URL), {"business": business.pk, "plan": plan.pk}
        )

        assert response.status_code == 302
        sub = FeaturedSubscription.objects.get(business=business)
        assert sub.is_active is True
        assert sub.plan_id == plan.pk
        assert sub.expires_at == sub.starts_at + timedelta(days=30)

    def test_add_post_supersedes_an_existing_active_subscription(self, admin_client):
        business = _make_business()
        first = activate_subscription(business, _make_plan("Featured 7", 7))
        plan_90 = _make_plan("Featured 90", 90)

        response = admin_client.post(
            reverse(ADD_URL), {"business": business.pk, "plan": plan_90.pk}
        )

        assert response.status_code == 302
        first.refresh_from_db()
        assert first.is_active is False
        assert FeaturedSubscription.objects.filter(business=business).count() == 2
        assert _active(business).count() == 1
        assert _active(business).get().plan_id == plan_90.pk

    def test_invalid_add_post_creates_nothing(self, admin_client):
        business = _make_business()

        response = admin_client.post(reverse(ADD_URL), {"business": business.pk})

        assert response.status_code == 200
        assert FeaturedSubscription.objects.count() == 0

    def test_change_page_is_read_only(self, admin_client):
        sub = activate_subscription(_make_business(), _make_plan())

        response = admin_client.get(
            reverse("admin:monetization_featuredsubscription_change", args=[sub.pk])
        )

        assert response.status_code == 200
        assert 'name="is_active"' not in response.content.decode()

    def test_reactivate_action_grants_a_fresh_subscription(self, admin_client):
        business = _make_business()
        old = activate_subscription(business, _make_plan())

        response = admin_client.post(
            reverse(CHANGELIST_URL),
            {"action": "reactivate_selected", "_selected_action": [old.pk]},
            follow=True,
        )

        assert response.status_code == 200
        old.refresh_from_db()
        assert old.is_active is False
        assert FeaturedSubscription.objects.filter(business=business).count() == 2
        assert _active(business).count() == 1

    def test_reactivate_action_dedupes_same_business_and_plan(self, admin_client):
        business = _make_business()
        plan = _make_plan()
        first = activate_subscription(business, plan)
        second = activate_subscription(business, plan)

        response = admin_client.post(
            reverse(CHANGELIST_URL),
            {
                "action": "reactivate_selected",
                "_selected_action": [first.pk, second.pk],
            },
            follow=True,
        )

        assert response.status_code == 200
        # 2 existing + exactly ONE new row, not two.
        assert FeaturedSubscription.objects.filter(business=business).count() == 3
        assert _active(business).count() == 1

    def test_deactivate_action_keeps_the_row_but_clears_the_flag(self, admin_client):
        business = _make_business()
        sub = activate_subscription(business, _make_plan())

        response = admin_client.post(
            reverse(CHANGELIST_URL),
            {"action": "deactivate_selected", "_selected_action": [sub.pk]},
            follow=True,
        )

        assert response.status_code == 200
        sub.refresh_from_db()
        assert sub.is_active is False
        assert _active(business).count() == 0
        assert FeaturedSubscription.objects.filter(pk=sub.pk).exists()