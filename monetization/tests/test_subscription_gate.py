"""BE-06: Subscription Gate, content hiding, /subscription/me."""

import pytest

from businesses.models import BusinessProfile
from core.tests.sweep_factories import client_for, make_business, make_user
from core.tests.sweep_helpers import assert_error_envelope
from monetization.services import is_locked
from monetization.tests.helpers import STATE_CASES, give_subscription
from monetization.visibility import exclude_locked_businesses

pytestmark = pytest.mark.django_db

ME_URL = "/api/v1/monetization/subscription/me/"
GATED_URLS = [
    "/api/v1/products/",
    "/api/v1/posts/",
    "/api/v1/reels/",
    "/api/v1/stories/",
]
ALWAYS_OPEN_URLS = [
    "/api/v1/monetization/plans/",
    "/api/v1/monetization/subscription/me/",
    "/api/v1/monetization/subscriptions/custom-quote/?gb=20",
]
CASE_IDS = [c[0] for c in STATE_CASES]


@pytest.mark.parametrize("url", GATED_URLS)
@pytest.mark.parametrize("case", STATE_CASES, ids=CASE_IDS)
def test_gate_blocks_only_locked_merchants(url, case):
    _, status, trial_days, period_days, locked = case
    business = make_business()
    give_subscription(business, status, trial_days, period_days)

    response = client_for(business.user).get(url)

    if locked:
        assert_error_envelope(response, 403, "SUBSCRIPTION_REQUIRED")
    else:
        assert response.status_code == 200


@pytest.mark.parametrize("url", GATED_URLS)
def test_customer_and_moderator_are_not_gated(url):
    customer = make_user()
    moderator = make_user()
    moderator.account_type = "moderator"
    moderator.save()

    assert client_for(customer).get(url).status_code == 200
    assert client_for(moderator).get(url).status_code == 200


@pytest.mark.parametrize("url", GATED_URLS)
def test_merchant_without_subscription_row_is_not_gated(url):
    from monetization.models import Subscription

    business = make_business()
    Subscription.objects.filter(business=business).delete()

    assert client_for(business.user).get(url).status_code == 200


@pytest.mark.parametrize("url", ALWAYS_OPEN_URLS)
def test_locked_merchant_can_still_reach_renewal_endpoints(url):
    business = make_business()
    give_subscription(business, "locked")

    assert client_for(business.user).get(url).status_code == 200


@pytest.mark.parametrize("case", STATE_CASES, ids=CASE_IDS)
def test_visibility_filter_matches_is_locked(case):
    _, status, trial_days, period_days, locked = case
    business = make_business()
    give_subscription(business, status, trial_days, period_days)

    visible = exclude_locked_businesses(BusinessProfile.objects.all(), prefix="")

    assert is_locked(business) is locked
    assert (business.pk in visible.values_list("pk", flat=True)) is (not locked)


def test_visibility_filter_keeps_businesses_without_subscription():
    business = make_business()

    visible = exclude_locked_businesses(BusinessProfile.objects.all(), prefix="")

    assert business.pk in visible.values_list("pk", flat=True)


def test_subscription_me_shape_and_values():
    business = make_business()
    give_subscription(business, "trialing", trial_days=5)

    response = client_for(business.user).get(ME_URL)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "plan", "status", "state", "days_left", "trial_ends_at",
        "current_period_end", "purge_at", "quota_bytes", "used_bytes",
        "is_featured_effective", "pending_plan", "locked",
    }
    assert body["plan"]["code"] == "free"
    assert body["state"] == "trialing"
    assert body["days_left"] in (5, 6)
    assert body["locked"] is False
    assert body["quota_bytes"] == 1024
    assert isinstance(body["used_bytes"], int)
    assert body["pending_plan"] is None


def test_subscription_me_reports_locked_state():
    business = make_business()
    give_subscription(business, "active", period_days=-1)

    body = client_for(business.user).get(ME_URL).json()

    assert body["state"] == "expired"
    assert body["locked"] is True
    assert body["days_left"] == 0


def test_subscription_me_without_subscription_is_404():
    from monetization.models import Subscription

    business = make_business()
    Subscription.objects.filter(business=business).delete()

    response = client_for(business.user).get(ME_URL)

    assert response.status_code == 404