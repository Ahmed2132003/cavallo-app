"""
BE-11: the whole storage-subscription life of a merchant, end to end.

new merchant -> trial -> upload to the limit -> rejected -> delete -> upload
-> checkout (fake gateway) -> paid activation (upgrade + featured) -> expiry
-> locked -> paid purge after 60 days; and a free trial -> locked -> purge
after 2 days. The notifications of the whole flow are collected at the end.

The webhook leg is covered by payments/tests; here activation is called
exactly as the webhook calls it.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from content.models import Post
from core.tests.test_media import _VALID_PNG_BYTES
from monetization.models import Plan, StoredFile, Subscription
from monetization.purge import purge_business_content
from monetization.services import create_free_subscription
from monetization.storage_usage import get_used_bytes
from monetization.tasks import expire_subscriptions
from monetization.tests.helpers import free_plan
from payments.models import Subscription as Payment
from payments.storage_activation import activate_storage_subscription
from payments.tests.factories import make_business, make_storage_plan

pytestmark = pytest.mark.django_db

PNG = _VALID_PNG_BYTES
SIZE = len(PNG)
GB = 1024**3
CHECKOUT = "/api/v1/payments/subscriptions/checkout/"


def _client(business):
    client = APIClient()
    client.force_authenticate(user=business.user)
    return client


def _upload(client):
    return client.post(
        "/api/v1/posts/",
        {"caption": "c", "image": SimpleUploadedFile("p.png", PNG, "image/png")},
        format="multipart",
    )


def _drain_on_commit():
    """Run every on_commit callback (the test transaction never commits),
    including the ones queued by callbacks."""
    while connection.run_on_commit:
        connection.run_on_commit.pop(0)[1]()


def test_full_storage_lifecycle(settings):
    settings.PAYMENT_GATEWAY = "payments.tests.fake_gateway.FakeGateway"
    free_plan()
    pro = make_storage_plan("pro", storage_gb=10)
    Plan.objects.filter(pk=pro.pk).update(is_featured=True)
    pro.refresh_from_db()

    with mock.patch("notifications.tasks.dispatch_notification.delay") as delay:
        # ---- 1. new merchant on a trial with a tiny quota
        merchant = make_business("lc-paid")
        create_free_subscription(merchant)
        sub = Subscription.objects.get(business=merchant)
        assert sub.status == "trialing"
        Subscription.objects.filter(pk=sub.pk).update(quota_bytes=2 * SIZE)
        client = _client(merchant)

        # ---- 2. fill the quota exactly, then get rejected
        first = _upload(client)
        assert first.status_code == 201, first.content
        assert _upload(client).status_code == 201
        rejected = _upload(client)
        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "STORAGE_QUOTA_EXCEEDED"
        assert Post.all_objects.filter(business=merchant).count() == 2

        # ---- 3. deleting frees the space, so the next upload fits
        assert client.delete(f"/api/v1/posts/{first.json()['id']}/").status_code == 204
        assert _upload(client).status_code == 201

        # ---- 4. checkout through the fake gateway, then paid activation
        checkout = client.post(CHECKOUT, {"plan": "pro"}, format="json")
        assert checkout.status_code == 201, checkout.content
        payment = Payment.objects.filter(business=merchant).order_by("-pk").first()
        assert payment.gateway_reference.startswith("fake-")
        activate_storage_subscription(merchant, pro, None, payment)

        sub.refresh_from_db()
        assert sub.status == "active"
        assert sub.plan_id == pro.pk
        assert sub.quota_bytes == 10 * GB
        assert sub.is_featured_effective is True
        assert sub.purge_at is None
        # the upgrade is immediate: an upload above the old quota now works
        assert _upload(client).status_code == 201

        # ---- 5. the period ends: expired, no longer featured, locked out
        end = timezone.now() - timedelta(hours=1)
        Subscription.objects.filter(pk=sub.pk).update(current_period_end=end)
        assert expire_subscriptions() == {"renewed": 0, "expired": 1, "locked": 0}
        sub.refresh_from_db()
        assert sub.status == "expired"
        assert sub.is_featured_effective is False
        assert sub.purge_at == end + timedelta(days=60)
        locked_out = _upload(client)
        assert locked_out.status_code == 403
        assert locked_out.json()["error"]["code"] == "SUBSCRIPTION_REQUIRED"

        # ---- 6. paid content is purged only after the 60-day grace
        assert purge_business_content(merchant).skipped_reason == "not_due"
        sub.refresh_from_db()
        # purge_blocker floors on the LATEST of trial end / period end, and the
        # purchase keeps the old trial date, so the grace counts from that one.
        last_end = max(d for d in (sub.trial_ends_at, sub.current_period_end) if d)
        report = purge_business_content(merchant, now=last_end + timedelta(days=61))
        assert report.purged is True
        assert Post.all_objects.filter(business=merchant).count() == 0
        assert StoredFile.objects.filter(business=merchant).count() == 0
        assert get_used_bytes(merchant) == 0

        # ---- 7. a free trial that was never paid: locked, purged after 2 days
        free = make_business("lc-free", phone_number="+201001234569")
        create_free_subscription(free)
        assert _upload(_client(free)).status_code == 201
        free_sub = Subscription.objects.get(business=free)
        trial_end = timezone.now() - timedelta(hours=1)
        Subscription.objects.filter(pk=free_sub.pk).update(
            trial_ends_at=trial_end, purge_at=trial_end + timedelta(days=2)
        )
        assert expire_subscriptions() == {"renewed": 0, "expired": 0, "locked": 1}
        free_sub.refresh_from_db()
        assert free_sub.status == "locked"
        assert purge_business_content(free).skipped_reason == "not_due"
        report = purge_business_content(free, now=trial_end + timedelta(days=3))
        assert report.purged is True
        assert Post.all_objects.filter(business=free).count() == 0

        # ---- 8. the notifications of the whole flow, in order, per owner
        _drain_on_commit()
        events = {}
        for call in delay.call_args_list:
            kwargs = call.kwargs
            if kwargs["notification_type"] != "subscription":
                continue
            events.setdefault(kwargs["recipient_id"], []).append(
                kwargs["params"]["event"]
            )
            assert kwargs["deep_link_type"] == "subscription_plans"

    assert events[merchant.user_id] == [
        "subscription_activated",
        "subscription_expired",
        "purge_done",
    ]
    assert events[free.user_id] == ["trial_expired", "purge_done"]


def test_a_failing_notification_never_breaks_a_paid_activation():
    pro = make_storage_plan("pro", storage_gb=10)
    merchant = make_business("lc-notify-fail")
    create_free_subscription(merchant)

    with mock.patch(
        "payments.storage_activation.notify_subscription_event",
        side_effect=RuntimeError("boom"),
    ):
        sub = activate_storage_subscription(merchant, pro, None, SimpleNamespace(pk=1))

    sub.refresh_from_db()
    assert sub.status == "active"
    assert sub.plan_id == pro.pk