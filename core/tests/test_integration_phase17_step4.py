"""
Part P-094 (Phase 17) - end-to-end integration test, walkthrough steps 11-12.

Step 11  Follow + Chat produce REAL notifications for the Business, they show
         up in the Notification Center API, and the (deep_link_type, target_id)
         pair each one carries resolves to a real screen for the recipient.
Step 12  An Admin activates Featured through the REAL Django Admin form; the
         business then ranks ahead of an otherwise-equivalent business in
         Search, in the Home feed's backfill tier and in Discover, the public
         profile reflects the flag at once, and the Admin "Deactivate" action
         reverts everything.

Deliberate notes:
- conftest.py's autouse `dispatch_delay` fixture mocks
  dispatch_notification.delay (no real Redis publish). To still prove the
  WHOLE seam, these tests take the exact kwargs the source (Follow view /
  chat offline task) handed to `.delay(...)` and run the real task body
  `dispatch_notification(**kwargs)` synchronously, exactly what a Celery
  worker would do. Firebase is not configured here, so the push half is
  skipped by design (live FCM delivery stays an open item from Section 7).
- Flutter's NotificationNavigator resolves "business_profile" to
  /business/:id (GET /api/v1/businesses/{id}/) and "chat_thread" by looking
  the id up in the conversation list (GET /api/v1/conversations/). The
  deep-link assertions below replay those two backend lookups.
- Search/Feed/Discover are asserted over HTTP. Home feed page 1 is cached
  for 90 s per user (P-060, accepted staleness), so a user who already
  fetched the feed is not used as the post-activation observer; see
  finding F-8 in INTEGRATION_TEST_REPORT_PHASE17.md.
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from categories.models import Category
from core.tests.test_integration_phase17 import (  # noqa: F401  (fixtures + helpers)
    _clear_cache,
    _feed_post_ids,
    _forced_client,
    _moderator_client,
    _publish_business,
    _register_and_login,
    _results,
    offline_notify,
)
from monetization.models import FeaturedSubscription, Plan
from notifications.models import Notification
from notifications.tasks import dispatch_notification

User = get_user_model()

pytestmark = pytest.mark.django_db


def _run_dispatch(dispatch_delay):
    """Run the real dispatch_notification body with the kwargs the source
    enqueued (what a Celery worker would do), then reset the mock."""
    assert dispatch_delay.call_count == 1, dispatch_delay.call_args_list
    kwargs = dispatch_delay.call_args.kwargs
    dispatch_notification(**kwargs)
    dispatch_delay.reset_mock()
    return kwargs


def _register_customer(email):
    client = _register_and_login(email, "customer")
    profile = client.post(
        "/api/v1/customers/me/",
        {"display_name": "S4 Customer", "country": "Egypt", "city": "Giza"},
        format="json",
    )
    assert profile.status_code in (200, 201), profile.content
    return client


class TestPhase17Step11Notifications:
    def test_follow_and_chat_notify_business_and_deep_link(
        self, offline_notify, dispatch_delay  # noqa: F811
    ):
        from chat.tasks import notify_offline_recipient as offline_task

        category = Category.objects.create(name="Fashion")
        b1 = _publish_business(
            "p094-s4-biz@example.com",
            name="Zephyr Atelier",
            city="Cairo",
            phone="+201001234567",
            product_name="Zephyr Jacket",
            price="199.50",
            caption="Zephyr new arrivals",
            category=category,
            moderator=_moderator_client(),
        )
        business = b1["client"]
        business_user = User.objects.get(email="p094-s4-biz@example.com")
        customer = _register_customer("p094-s4-customer@example.com")

        # Moderation of the Post (step 3) is itself a notification source
        # (P-079); those calls happened inside _publish_business and were
        # mocked. Start this step from a clean slate.
        dispatch_delay.reset_mock()
        assert _results(business.get("/api/v1/notifications/")) == []

        # ---- STEP 11a: Follow -> new_follower notification ------------------
        followed = customer.post(f"/api/v1/businesses/{b1['business_id']}/follow/")
        assert followed.status_code == 200, followed.content
        kwargs = _run_dispatch(dispatch_delay)
        assert kwargs["recipient_id"] == business_user.id
        assert kwargs["notification_type"] == "new_follower"
        assert kwargs["deep_link_type"] == "business_profile"
        assert kwargs["target_id"] == b1["business_id"]

        # An idempotent repeat must NOT notify again.
        customer.post(f"/api/v1/businesses/{b1['business_id']}/follow/")
        assert dispatch_delay.call_count == 0

        listing = _results(business.get("/api/v1/notifications/"))
        assert len(listing) == 1
        follow_notif = listing[0]
        assert follow_notif["notification_type"] == "new_follower"
        assert follow_notif["deep_link_type"] == "business_profile"
        assert follow_notif["target_id"] == b1["business_id"]
        assert follow_notif["is_read"] is False

        # Deep link: /business/:id -> the public profile of THAT business.
        profile = business.get(f"/api/v1/businesses/{follow_notif['target_id']}/")
        assert profile.status_code == 200, profile.content
        assert profile.json()["business_name"] == "Zephyr Atelier"

        # The customer did not receive the business's notification.
        assert _results(customer.get("/api/v1/notifications/")) == []

        # ---- STEP 11b: Chat (recipient offline) -> chat_message --------------
        start = customer.post(
            "/api/v1/conversations/start/",
            {"business_id": b1["business_id"]},
            format="json",
        )
        assert start.status_code == 201, start.content
        conversation_id = start.json()["id"]

        sent = customer.post(
            f"/api/v1/conversations/{conversation_id}/messages/",
            {"text": "Is the jacket available?"},
            format="json",
        )
        assert sent.status_code == 201, sent.content
        message_id = sent.json()["id"]
        # The view decided the recipient is offline and queued the task.
        offline_notify.delay.assert_called_once_with(message_id)

        # Run the real offline task, then the real dispatch it enqueues.
        offline_task(message_id)
        kwargs = _run_dispatch(dispatch_delay)
        assert kwargs["recipient_id"] == business_user.id
        assert kwargs["notification_type"] == "chat_message"
        assert kwargs["deep_link_type"] == "chat_thread"
        assert kwargs["target_id"] == conversation_id
        assert kwargs["body"] == "Is the jacket available?"

        listing = _results(business.get("/api/v1/notifications/"))
        assert [n["notification_type"] for n in listing] == [
            "chat_message",
            "new_follower",
        ]  # newest first
        chat_notif = listing[0]
        assert chat_notif["deep_link_type"] == "chat_thread"
        assert chat_notif["target_id"] == conversation_id

        # Deep link: Flutter finds the conversation in the caller's list.
        conversations = _results(business.get("/api/v1/conversations/"))
        assert chat_notif["target_id"] in [c["id"] for c in conversations]
        # And the thread itself is readable by the recipient.
        thread = business.get(
            f"/api/v1/conversations/{chat_notif['target_id']}/messages/"
        )
        assert thread.status_code == 200, thread.content

        # The SENDER gets no notification for their own message.
        assert _results(customer.get("/api/v1/notifications/")) == []

        # ---- Mark read: only own, idempotent, never un-readable ---------------
        marked = business.patch(f"/api/v1/notifications/{chat_notif['id']}/read/")
        assert marked.status_code == 200
        assert marked.json()["is_read"] is True
        assert (
            business.patch(f"/api/v1/notifications/{chat_notif['id']}/read/").json()[
                "is_read"
            ]
            is True
        )
        assert (
            customer.patch(
                f"/api/v1/notifications/{chat_notif['id']}/read/"
            ).status_code
            == 404
        )
        unread = [
            n
            for n in _results(business.get("/api/v1/notifications/"))
            if not n["is_read"]
        ]
        assert [n["notification_type"] for n in unread] == ["new_follower"]

        # ---- Preference seam (P-078 -> P-079): muted category = no row --------
        muted = business.patch(
            "/api/v1/notifications/preferences/",
            {"chat_notifications_enabled": False},
            format="json",
        )
        assert muted.status_code == 200, muted.content

        before = Notification.objects.filter(recipient=business_user).count()
        second = customer.post(
            f"/api/v1/conversations/{conversation_id}/messages/",
            {"text": "Hello?"},
            format="json",
        )
        assert second.status_code == 201, second.content
        offline_notify.reset_mock()
        offline_task(second.json()["id"])
        _run_dispatch(dispatch_delay)
        assert Notification.objects.filter(recipient=business_user).count() == before


def _business_order(response):
    assert response.status_code == 200, response.content
    return [
        item["id"]
        for item in response.json()["items"]
        if item["result_type"] == "business"
    ]


class TestPhase17Step12Featured:
    def test_admin_activates_featured_and_ranking_follows(
        self, admin_client, django_capture_on_commit_callbacks
    ):
        category = Category.objects.create(name="Fashion")
        moderator = _moderator_client()
        # Same searchable token ("Zephyr") on both, so they are otherwise
        # equivalent for the text query. b2 is created second, so its Post
        # is the NEWER one (backfill order is newest-first).
        b1 = _publish_business(
            "p094-s4-f1@example.com",
            name="Zephyr Atelier One",
            city="Cairo",
            phone="+201001234567",
            product_name="Zephyr Jacket",
            price="199.50",
            caption="Zephyr first",
            category=category,
            moderator=moderator,
        )
        b2 = _publish_business(
            "p094-s4-f2@example.com",
            name="Zephyr Atelier Two",
            city="Cairo",
            phone="+201001234568",
            product_name="Zephyr Scarf",
            price="40.00",
            caption="Zephyr second",
            category=category,
            moderator=moderator,
        )
        customer = _register_customer("p094-s4-f-customer@example.com")
        plan = Plan.objects.create(
            name="Featured 30",
            duration_days=30,
            price=Decimal("250.00"),
            currency="EGP",
        )

        # ---- Baseline (nothing featured) -------------------------------------
        base_search = _business_order(customer.get("/api/v1/search/", {"q": "Zephyr"}))
        assert set(base_search) == {b1["business_id"], b2["business_id"]}
        # Feature whichever business would otherwise rank LAST, so the
        # assertion cannot pass by coincidence.
        target_id = base_search[-1]
        target = b1 if b1["business_id"] == target_id else b2
        other = b2 if target is b1 else b1

        home_before = _feed_post_ids(customer.get("/api/v1/feed/home/"))
        # Prime the public-profile cache (P-030, 5 min) the way a real
        # profile screen would.
        profile_url = f"/api/v1/businesses/{target['business_id']}/"
        assert APIClient().get(profile_url).json()["is_featured"] is False
        assert home_before.index(b2["post_id"]) < home_before.index(b1["post_id"])

        # ---- STEP 12: Admin activates Featured via the REAL Django Admin ------
        with django_capture_on_commit_callbacks(execute=True):
            response = admin_client.post(
                reverse("admin:monetization_featuredsubscription_add"),
                {"business": target["business_id"], "plan": plan.id},
            )
        assert response.status_code == 302, response.content[:500]
        subscription = FeaturedSubscription.objects.get(
            business_id=target["business_id"], is_active=True
        )

        # Search: featured first, and the organic business is still there.
        after_search = _business_order(customer.get("/api/v1/search/", {"q": "Zephyr"}))
        assert after_search[0] == target["business_id"]
        assert other["business_id"] in after_search

        # Seam Admin -> public profile cache (finding F-9).
        assert APIClient().get(profile_url).json()["is_featured"] is True, (
            "SEAM GAP (F-9): Featured was activated but the cached public "
            "profile still says is_featured=false"
        )

        # Home feed backfill tier + Discover. A fresh observer is used for
        # Home (the first customer's page 1 is cached for 90 s, see F-8).
        observer = _forced_client(
            User.objects.create_user(
                username="p094-s4-observer",
                email="p094-s4-observer@example.com",
                password="Str0ng!Passw0rd#94",
                account_type=User.ACCOUNT_TYPE_CUSTOMER,
            )
        )
        home_after = _feed_post_ids(observer.get("/api/v1/feed/home/"))
        assert home_after.index(target["post_id"]) < home_after.index(other["post_id"])
        discover = _feed_post_ids(customer.get("/api/v1/feed/discover/"))
        assert discover.index(target["post_id"]) < discover.index(other["post_id"])

        # ---- Admin "Deactivate selected" reverts everything --------------------
        with django_capture_on_commit_callbacks(execute=True):
            reverted = admin_client.post(
                reverse("admin:monetization_featuredsubscription_changelist"),
                {
                    "action": "deactivate_selected",
                    "_selected_action": [subscription.pk],
                },
            )
        assert reverted.status_code == 302, reverted.content[:500]
        assert not FeaturedSubscription.objects.filter(
            business_id=target["business_id"], is_active=True
        ).exists()
        assert APIClient().get(profile_url).json()["is_featured"] is False
        assert _business_order(customer.get("/api/v1/search/", {"q": "Zephyr"})) == (
            base_search
        )