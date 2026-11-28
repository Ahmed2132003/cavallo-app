"""BE-11: manual activation action of the Subscription admin."""

from datetime import timedelta

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages.storage.cookie import CookieStorage
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from monetization.admin import SubscriptionAdmin
from monetization.models import Subscription
from monetization.services import custom_quota_bytes
from monetization.tests.helpers import give_subscription
from payments.tests.factories import make_business, make_storage_plan

User = get_user_model()

pytestmark = pytest.mark.django_db

GB = 1024**3
CHANGELIST = "admin:monetization_subscription_changelist"


def _messages(response):
    """Admin messages of the (followed) response, to explain a failure."""
    return [str(m) for m in response.context["messages"]]


def _activate(client, sub, plan=None, gb=""):
    return client.post(
        reverse(CHANGELIST),
        {
            "action": "activate_manually",
            "_selected_action": [sub.pk],
            "plan": plan.pk if plan is not None else "",
            "gb": gb,
        },
        follow=True,
    )


def _call_action_directly(user, sub, plan, gb=""):
    """Bypass the ActionForm: run the admin action itself."""
    request = RequestFactory().post("/", {"plan": plan.pk, "gb": gb})
    request.user = user
    request._messages = CookieStorage(request)
    model_admin = SubscriptionAdmin(Subscription, admin.site)
    model_admin.activate_manually(request, Subscription.objects.filter(pk=sub.pk))
    return [str(m) for m in request._messages]


def test_action_form_accepts_the_posted_fields(admin_user):
    """If this fails, the message names the field Django rejects."""
    pro = make_storage_plan("pro", storage_gb=10)
    request = RequestFactory().post("/")
    request.user = admin_user
    model_admin = SubscriptionAdmin(Subscription, admin.site)
    form = model_admin.action_form(
        {"action": "activate_manually", "plan": str(pro.pk), "gb": ""}, auto_id=None
    )
    form.fields["action"].choices = model_admin.get_action_choices(request)
    assert form.is_valid(), form.errors.as_json()


def test_action_logic_activates_when_called_directly(admin_user):
    pro = make_storage_plan("pro", storage_gb=10)
    sub = give_subscription(make_business("act0"), "locked", trial_days=-5)

    messages = _call_action_directly(admin_user, sub, pro)

    sub.refresh_from_db()
    assert sub.status == "active", messages
    assert sub.plan_id == pro.pk


def test_locked_trial_is_activated_on_the_chosen_plan(admin_client):
    pro = make_storage_plan("pro", storage_gb=10)
    sub = give_subscription(make_business("act1"), "locked", trial_days=-5)
    Subscription.objects.filter(pk=sub.pk).update(
        purge_at=timezone.now() + timedelta(days=1)
    )

    response = _activate(admin_client, sub, pro)
    assert response.status_code == 200

    sub.refresh_from_db()
    assert sub.status == "active", _messages(response)
    assert sub.plan_id == pro.pk
    assert sub.quota_bytes == 10 * GB
    assert sub.purge_at is None
    assert sub.current_period_end - sub.current_period_start == timedelta(days=30)


def test_running_trial_can_be_activated_too(admin_client):
    pro = make_storage_plan("pro", storage_gb=10)
    sub = give_subscription(make_business("act2"), "trialing", trial_days=5)

    response = _activate(admin_client, sub, pro)

    sub.refresh_from_db()
    assert sub.status == "active" and sub.plan_id == pro.pk, _messages(response)


def test_without_a_plan_nothing_changes(admin_client):
    sub = give_subscription(make_business("act3"), "locked", trial_days=-5)

    _activate(admin_client, sub, None)

    sub.refresh_from_db()
    assert sub.status == "locked"


def test_the_free_plan_cannot_be_chosen(admin_client):
    sub = give_subscription(make_business("act4"), "locked", trial_days=-5)
    free = sub.plan

    _activate(admin_client, sub, free)

    sub.refresh_from_db()
    assert sub.status == "locked"


def test_custom_plan_needs_a_valid_gb(admin_client):
    custom = make_storage_plan(
        "infinity", price_egp=None, storage_gb=None, is_custom=True
    )
    sub = give_subscription(make_business("act5"), "locked", trial_days=-5)

    for bad in ("", "abc", "1", "9999"):
        _activate(admin_client, sub, custom, gb=bad)
        sub.refresh_from_db()
        assert sub.status == "locked", bad


def test_custom_plan_with_gb_gets_the_exact_quota(admin_client):
    custom = make_storage_plan(
        "infinity", price_egp=None, storage_gb=None, is_custom=True
    )
    sub = give_subscription(make_business("act6"), "locked", trial_days=-5)

    response = _activate(admin_client, sub, custom, gb="20")

    sub.refresh_from_db()
    assert sub.status == "active", _messages(response)
    assert sub.quota_bytes == custom_quota_bytes("20")
    assert sub.custom_gb == 20


def test_view_only_staff_cannot_activate():
    pro = make_storage_plan("pro", storage_gb=10)
    sub = give_subscription(make_business("act7"), "locked", trial_days=-5)
    staff = User.objects.create_user(
        username="viewer2@example.com",
        email="viewer2@example.com",
        password="testpass123",
        account_type="customer",
        is_staff=True,
    )
    staff.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="monetization", codename="view_subscription"
        )
    )
    client = Client()
    client.force_login(staff)

    _activate(client, sub, pro)

    sub.refresh_from_db()
    assert sub.status == "locked"