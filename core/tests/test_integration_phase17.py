"""
Part P-094 (Phase 17) - end-to-end integration test, walkthrough steps 1-4.

Drives the REAL HTTP API through several phase boundaries in one
sequential test (Auth -> Business onboarding -> Admin verify -> Product /
Post / Story creation -> Moderator approval -> Customer registration),
asserting on the seams between phases rather than on any single phase's
own behaviour. Later P-094 steps extend this same module (Search / Feed /
Follow / Engage / Rating in step 2 of the script series, Chat /
Notifications / Featured in step 3).

Deliberate notes:
- "Admin verifies the business" is done by saving
  User.is_business_verified because verification is a Django Admin action
  (no public API exists for it). It uses user.save() (what Django Admin
  does), after priming the public-profile cache, to guard finding F-2.
  The manual walkthrough still covers the real Django Admin toggle.
- Products are NOT moderated in this codebase (products.models.Product is
  not a moderation.Moderatable). The test records this explicitly instead
  of assuming the plan's "moderator approves all three" applies to
  Products; see INTEGRATION_TEST_REPORT_PHASE17.md finding F-1.
- conftest.py's autouse dispatch_delay fixture already mocks
  notifications.tasks.dispatch_notification.delay, so nothing here
  publishes to the real Redis broker.
"""

import json
from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from rest_framework_simplejwt.tokens import AccessToken

from chat.consumers import presence_cache_key
from chat.models import Message
from config.asgi import application

from businesses.models import BusinessProfile
from categories.models import Category
from content.models import Post
from core.tests.test_media import _VALID_PNG_BYTES
from moderation.models import Moderatable, ModerationQueue
from products.models import Product
from stories.models import Story

User = get_user_model()

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ng!Passw0rd#94"


@pytest.fixture(autouse=True)
def _clear_cache():
    # Redis is shared across test runs and the test DB restarts ids, so a
    # stale "business_profile:{id}" entry (P-030, 5 min TTL) from an earlier
    # run/test could be served here. LoginRateThrottle (5/min per IP, P-018)
    # also lives in this cache and this test logs in 3 times. Same pattern as
    # accounts/tests/test_auth.py.
    cache.clear()
    yield
    cache.clear()


def _png(name):
    return SimpleUploadedFile(name, _VALID_PNG_BYTES, content_type="image/png")


def _results(response):
    data = response.json()
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return data


def _ids(response):
    return [item["id"] for item in _results(response)]


def _login(email):
    client = APIClient()
    response = client.post(
        "/api/v1/auth/login/",
        {"email": email, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200, response.content
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['access']}")
    return client


def _register_and_login(email, account_type):
    response = APIClient().post(
        "/api/v1/auth/register/",
        {
            "email": email,
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "account_type": account_type,
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["account_type"] == account_type
    return _login(email)


def _queue_item_for(obj):
    return ModerationQueue.objects.get(
        content_type=ContentType.objects.get_for_model(obj.__class__),
        object_id=obj.pk,
    )


class TestPhase17Steps1To4:
    def test_register_onboard_verify_publish_moderate_register_customer(self):
        category = Category.objects.create(name="Fashion")

        # ---- STEP 1a: register a Business account --------------------------
        business_client = _register_and_login("p094-biz@example.com", "business")
        me = business_client.get("/api/v1/auth/me/")
        assert me.status_code == 200
        assert me.json()["account_type"] == "business"

        # ---- STEP 1b: onboarding (P-028) -----------------------------------
        onboarding = business_client.post(
            "/api/v1/businesses/me/",
            {
                "business_name": "P094 Atelier",
                "business_type": "trader",
                "country": "Egypt",
                "city": "Cairo",
                "description": "Integration pass business",
                "category": category.id,
                "phone_number": "+201001234567",
            },
            format="json",
        )
        assert onboarding.status_code == 201, onboarding.content
        business_id = onboarding.json()["id"]
        assert onboarding.json()["is_verified"] is False

        # ---- STEP 1c: Admin verifies the business --------------------------
        # Prime the public-profile cache FIRST (P-030 caches it for 5 min);
        # this is exactly what happens in real life when a customer opens
        # the profile before the Admin verifies it.
        before = APIClient().get(f"/api/v1/businesses/{business_id}/")
        assert before.status_code == 200
        assert before.json()["is_verified"] is False

        # Django Admin saves the User through Model.save(), so mimic that
        # (NOT QuerySet.update(), which would skip signals/cache invalidation).
        business_user = User.objects.get(email="p094-biz@example.com")
        business_user.is_business_verified = True
        business_user.save()

        public_profile = APIClient().get(f"/api/v1/businesses/{business_id}/")
        assert public_profile.status_code == 200
        assert public_profile.json()["is_verified"] is True, (
            "SEAM GAP (F-2): is_business_verified toggled on User but the "
            "cached public BusinessProfile still shows unverified"
        )

        # ---- STEP 2: Product, Post, Story ----------------------------------
        product_response = business_client.post(
            "/api/v1/products/",
            {
                "name": "P094 Jacket",
                "description": "Integration pass product",
                "price": "199.50",
                "currency": "EGP",
                "category": category.id,
                "image": _png("product.png"),
            },
            format="multipart",
        )
        assert product_response.status_code == 201, product_response.content
        product_id = product_response.json()["id"]

        post_response = business_client.post(
            "/api/v1/posts/",
            {"caption": "P094 first post", "image": _png("post.png")},
            format="multipart",
        )
        assert post_response.status_code == 201, post_response.content
        post_id = post_response.json()["id"]
        assert post_response.json()["status"] == Moderatable.Status.PENDING_REVIEW

        story_response = business_client.post(
            "/api/v1/stories/",
            {"media": _png("story.png")},
            format="multipart",
        )
        assert story_response.status_code == 201, story_response.content
        story_id = story_response.json()["id"]
        assert story_response.json()["status"] == Moderatable.Status.PENDING_REVIEW

        # Pending content must NOT be publicly visible yet (Section 9 gate).
        anon = APIClient()
        assert post_id not in _ids(anon.get("/api/v1/posts/public/"))
        assert story_id not in _ids(anon.get("/api/v1/stories/public/"))

        # Product is not moderated: finding F-1 in the report.
        assert not issubclass(Product, Moderatable)
        assert product_id in _ids(anon.get("/api/v1/products/public/"))

        # ---- STEP 3: Moderator reviews and approves ------------------------
        moderator = User.objects.create_user(
            username="p094-moderator",
            email="p094-moderator@example.com",
            password=PASSWORD,
            account_type=User.ACCOUNT_TYPE_CUSTOMER,
        )
        moderator.groups.add(Group.objects.get(name="Moderator"))
        moderator_client = _login("p094-moderator@example.com")

        post = Post.objects.get(pk=post_id)
        story = Story.objects.get(pk=story_id)
        post_item = _queue_item_for(post)
        story_item = _queue_item_for(story)

        queue = moderator_client.get("/api/v1/moderation/queue/")
        assert queue.status_code == 200
        queued_ids = _ids(queue)
        assert post_item.pk in queued_ids
        assert story_item.pk in queued_ids

        for item in (post_item, story_item):
            approved = moderator_client.post(
                f"/api/v1/moderation/queue/{item.pk}/approve/"
            )
            assert approved.status_code == 200, approved.content

        post.refresh_from_db()
        story.refresh_from_db()
        assert post.status == Moderatable.Status.PUBLISHED
        assert story.status == Moderatable.Status.PUBLISHED

        # Business owner now sees the approved status through its own API.
        owner_post = business_client.get(f"/api/v1/posts/{post_id}/")
        assert owner_post.status_code == 200
        assert owner_post.json()["status"] == Moderatable.Status.PUBLISHED

        # ---- STEP 4: Customer registers ------------------------------------
        customer_client = _register_and_login("p094-customer@example.com", "customer")
        customer_me = customer_client.get("/api/v1/auth/me/")
        assert customer_me.json()["account_type"] == "customer"
        customer_profile = customer_client.post(
            "/api/v1/customers/me/",
            {"display_name": "P094 Customer", "country": "Egypt", "city": "Giza"},
            format="json",
        )
        assert customer_profile.status_code in (200, 201), customer_profile.content

        # Seam check that step 5/6 build on: the customer already sees the
        # approved Post and Story through the PUBLIC endpoints.
        assert post_id in _ids(customer_client.get("/api/v1/posts/public/"))
        assert story_id in _ids(customer_client.get("/api/v1/stories/public/"))
        assert customer_client.get(f"/api/v1/businesses/{business_id}/").status_code == 200


# ===========================================================================
# STEP 2 of the P-094 script series: walkthrough steps 5-8
# (Search + Discover, Follow + Home feed, Like/Comment/Save/Share, Rating).
# ===========================================================================


def _moderator_client():
    # force_authenticate (not a real login) keeps this test under the
    # 5/min LoginRateThrottle; the Moderator group check still runs for real.
    moderator = User.objects.create_user(
        username="p094-s2-moderator",
        email="p094-s2-moderator@example.com",
        password=PASSWORD,
        account_type=User.ACCOUNT_TYPE_CUSTOMER,
    )
    moderator.groups.add(Group.objects.get(name="Moderator"))
    client = APIClient()
    client.force_authenticate(user=moderator)
    return client


def _publish_business(
    email, *, name, city, phone, product_name, price, caption, category, moderator
):
    """Register + onboard a Business, create a Product and a Post through the
    real API, and have the Moderator approve the Post. Returns ids + client."""
    client = _register_and_login(email, "business")
    onboarding = client.post(
        "/api/v1/businesses/me/",
        {
            "business_name": name,
            "business_type": "trader",
            "country": "Egypt",
            "city": city,
            "description": "Integration pass business",
            "category": category.id,
            "phone_number": phone,
        },
        format="json",
    )
    assert onboarding.status_code == 201, onboarding.content
    business_id = onboarding.json()["id"]

    product = client.post(
        "/api/v1/products/",
        {
            "name": product_name,
            "description": "Integration pass product",
            "price": price,
            "currency": "EGP",
            "category": category.id,
            "image": _png("product.png"),
        },
        format="multipart",
    )
    assert product.status_code == 201, product.content

    post = client.post(
        "/api/v1/posts/",
        {"caption": caption, "image": _png("post.png")},
        format="multipart",
    )
    assert post.status_code == 201, post.content
    post_id = post.json()["id"]

    item = _queue_item_for(Post.objects.get(pk=post_id))
    approved = moderator.post(f"/api/v1/moderation/queue/{item.pk}/approve/")
    assert approved.status_code == 200, approved.content

    return {
        "client": client,
        "business_id": business_id,
        "product_id": product.json()["id"],
        "post_id": post_id,
    }


def _search(client, **params):
    response = client.get("/api/v1/search/", params)
    assert response.status_code == 200, response.content
    return {(item["result_type"], item["id"]) for item in response.json()["items"]}


def _feed_post_ids(response):
    assert response.status_code == 200, response.content
    return [
        item["id"]
        for item in response.json()["items"]
        if item["content_type"] == "post"
    ]


class TestPhase17Steps5To8:
    def test_search_discover_follow_feed_engage_rate(self):
        category = Category.objects.create(name="Fashion")
        moderator = _moderator_client()

        # Two published businesses. B2 is created second, so its Post is the
        # NEWER one (the feed's backfill order is newest-first).
        b1 = _publish_business(
            "p094-s2-b1@example.com",
            name="Zephyr Atelier",
            city="Cairo",
            phone="+201001234567",
            product_name="Zephyr Jacket",
            price="199.50",
            caption="Zephyr new arrivals",
            category=category,
            moderator=moderator,
        )
        b2 = _publish_business(
            "p094-s2-b2@example.com",
            name="Other Boutique",
            city="Alexandria",
            phone="+201001234568",
            product_name="Plain Scarf",
            price="40.00",
            caption="Other boutique news",
            category=category,
            moderator=moderator,
        )

        # ---- STEP 4 (carried over): the Customer ---------------------------
        customer = _register_and_login("p094-s2-customer@example.com", "customer")
        profile = customer.post(
            "/api/v1/customers/me/",
            {"display_name": "S2 Customer", "country": "Egypt", "city": "Giza"},
            format="json",
        )
        assert profile.status_code in (200, 201), profile.content

        # ---- STEP 5a: Search (text query + filters) -------------------------
        found = _search(customer, q="Zephyr")
        assert ("business", b1["business_id"]) in found
        assert ("product", b1["product_id"]) in found
        assert ("business", b2["business_id"]) not in found
        assert ("product", b2["product_id"]) not in found

        found = _search(customer, q="Zephyr", country="Egypt", category=category.id)
        assert ("business", b1["business_id"]) in found

        assert _search(customer, q="Zephyr", country="Narnia") == set()

        # Filter without a text query (recency mode): city narrows to B1.
        found = _search(customer, city="Cairo")
        assert ("business", b1["business_id"]) in found
        assert ("product", b1["product_id"]) in found
        assert ("business", b2["business_id"]) not in found

        # Price filter applies to the product (199.50) only.
        assert ("product", b1["product_id"]) in _search(customer, min_price="100")
        assert ("product", b1["product_id"]) not in _search(customer, min_price="500")

        # ---- STEP 5b: Discover feed ------------------------------------------
        discover = customer.get("/api/v1/feed/discover/")
        discover_posts = _feed_post_ids(discover)
        assert b1["post_id"] in discover_posts
        assert b2["post_id"] in discover_posts

        # ---- STEP 6: Follow -> Home feed on a FRESH fetch ----------------------
        # Before following: nothing is followed, so the whole feed is the
        # backfill tier, newest first -> B2's Post, then B1's. This request
        # also primes the per-user first-page feed cache (P-060, 90 s).
        before = _feed_post_ids(customer.get("/api/v1/feed/home/"))
        assert before.index(b2["post_id"]) < before.index(b1["post_id"])

        followed = customer.post(f"/api/v1/businesses/{b1['business_id']}/follow/")
        assert followed.status_code == 200, followed.content
        assert followed.json() == {"following": True}
        assert BusinessProfile.objects.get(pk=b1["business_id"]).follower_count == 1

        # Hybrid algorithm: followed business content comes FIRST (following
        # tier), the rest is backfill, and nothing appears twice.
        after = _feed_post_ids(customer.get("/api/v1/feed/home/"))
        assert len(after) == len(set(after))
        assert after.index(b1["post_id"]) < after.index(b2["post_id"]), (
            "SEAM GAP (F-5): the customer followed B1 but the Home feed still "
            "shows the pre-follow (cached) order"
        )

        # Unfollow flips it back immediately; re-follow for the rest of the walk.
        unfollowed = customer.delete(f"/api/v1/businesses/{b1['business_id']}/follow/")
        assert unfollowed.json() == {"following": False}
        again = _feed_post_ids(customer.get("/api/v1/feed/home/"))
        assert again.index(b2["post_id"]) < again.index(b1["post_id"])
        customer.post(f"/api/v1/businesses/{b1['business_id']}/follow/")
        refollowed = _feed_post_ids(customer.get("/api/v1/feed/home/"))
        assert refollowed.index(b1["post_id"]) < refollowed.index(b2["post_id"])
        assert BusinessProfile.objects.get(pk=b1["business_id"]).follower_count == 1

        # ---- STEP 7: Like / Comment / Save / Share -----------------------------
        like = {"content_type": "post", "object_id": b1["post_id"]}
        assert customer.post("/api/v1/likes/", like, format="json").json() == {
            "liked": True
        }
        # Idempotent: a second like must not double-count.
        customer.post("/api/v1/likes/", like, format="json")

        comment = customer.post(
            "/api/v1/comments/",
            {"content_type": "post", "object_id": b1["post_id"], "text": "Nice!"},
            format="json",
        )
        assert comment.status_code == 201, comment.content

        save = customer.post(
            "/api/v1/saves/",
            {"content_type": "product", "object_id": b1["product_id"]},
            format="json",
        )
        assert save.json() == {"saved": True}

        share = customer.post(
            "/api/v1/shares/",
            {"content_type": "post", "object_id": b1["post_id"]},
            format="json",
        )
        assert share.status_code == 201, share.content

        public_posts = _results(
            APIClient().get(f"/api/v1/posts/public/?business_id={b1['business_id']}")
        )
        shown = next(p for p in public_posts if p["id"] == b1["post_id"])
        assert shown["likes_count"] == 1
        assert shown["comments_count"] == 1
        assert shown["shares_count"] == 1

        saved = _results(customer.get("/api/v1/saves/me/"))
        assert any(
            s["content_type"] == "product" and s["object_id"] == b1["product_id"]
            for s in saved
        )

        # ---- STEP 8: Rating -----------------------------------------------------
        rate_url = f"/api/v1/businesses/{b1['business_id']}/rate/"
        assert customer.post(rate_url, {"score": 6}, format="json").status_code == 400

        first = customer.post(
            rate_url, {"score": 4, "review_text": "Great"}, format="json"
        )
        assert first.status_code == 200, first.content
        assert Decimal(str(first.json()["average_rating"])) == Decimal("4")
        assert first.json()["ratings_count"] == 1

        second_user = User.objects.create_user(
            username="p094-s2-customer2",
            email="p094-s2-customer2@example.com",
            password=PASSWORD,
            account_type=User.ACCOUNT_TYPE_CUSTOMER,
        )
        second = APIClient()
        second.force_authenticate(user=second_user)
        res = second.post(rate_url, {"score": 2}, format="json")
        assert Decimal(str(res.json()["average_rating"])) == Decimal("3")
        assert res.json()["ratings_count"] == 2

        # Upsert: the first customer changes 4 -> 5; count stays 2, avg 3.5.
        res = customer.post(rate_url, {"score": 5, "review_text": "Better"}, format="json")
        assert Decimal(str(res.json()["average_rating"])) == Decimal("3.5")
        assert res.json()["ratings_count"] == 2

        business = BusinessProfile.objects.get(pk=b1["business_id"])
        assert business.average_rating == Decimal("3.5")
        assert business.ratings_count == 2
        other = BusinessProfile.objects.get(pk=b2["business_id"])
        assert other.ratings_count == 0

        reviews = _results(APIClient().get(f"/api/v1/businesses/{b1['business_id']}/ratings/"))
        assert sorted(r["score"] for r in reviews) == [2, 5]

        # Seam Rating -> Search: min_rating reads the recomputed aggregate.
        assert ("business", b1["business_id"]) in _search(customer, min_rating="3")
        assert ("business", b1["business_id"]) not in _search(customer, min_rating="4")


# ===========================================================================
# STEP 3 of the P-094 script series: walkthrough steps 9-10
# (Chat: text / image / shared product; fetch-on-open; live delivery and the
# sent -> delivered -> read progression over the real WebSocket stack).
# ===========================================================================


@pytest.fixture
def offline_notify():
    # MessageSendView calls notify_offline_recipient.delay() when the other
    # participant is not connected. Without this patch that would publish a
    # real Celery message to the shared Redis broker (and the running docker
    # worker would execute it against the dev database).
    with mock.patch("chat.views.notify_offline_recipient") as patched:
        yield patched


def _forced_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class TestPhase17Steps9To10Rest:
    def test_message_business_text_image_and_shared_product(
        self, offline_notify, dispatch_delay
    ):
        from chat.tasks import notify_offline_recipient as offline_task

        category = Category.objects.create(name="Fashion")
        b1 = _publish_business(
            "p094-s3-biz@example.com",
            name="Zephyr Atelier",
            city="Cairo",
            phone="+201001234567",
            product_name="Zephyr Jacket",
            price="199.50",
            caption="Zephyr new arrivals",
            category=category,
            moderator=_moderator_client(),
        )
        business_user = User.objects.get(email="p094-s3-biz@example.com")

        customer = _register_and_login("p094-s3-customer@example.com", "customer")
        profile = customer.post(
            "/api/v1/customers/me/",
            {"display_name": "S3 Customer", "country": "Egypt", "city": "Giza"},
            format="json",
        )
        assert profile.status_code in (200, 201), profile.content

        # ---- STEP 9a: start the conversation from the business page ----------
        start = customer.post(
            "/api/v1/conversations/start/",
            {"business_id": b1["business_id"]},
            format="json",
        )
        assert start.status_code == 201, start.content
        conversation_id = start.json()["id"]
        assert business_user.id in start.json()["participant_ids"]

        again = customer.post(
            "/api/v1/conversations/start/",
            {"business_id": b1["business_id"]},
            format="json",
        )
        assert again.status_code == 200
        assert again.json()["id"] == conversation_id

        own = b1["client"].post(
            "/api/v1/conversations/start/",
            {"business_id": b1["business_id"]},
            format="json",
        )
        assert own.status_code == 400

        messages_url = f"/api/v1/conversations/{conversation_id}/messages/"

        # ---- STEP 9b: text, image, shared product card ------------------------
        text = customer.post(
            messages_url, {"text": "Is the jacket available?"}, format="json"
        )
        assert text.status_code == 201, text.content
        assert text.json()["status"] == "sent"
        text_id = text.json()["id"]

        image = customer.post(
            messages_url, {"media": _png("chat.png")}, format="multipart"
        )
        assert image.status_code == 201, image.content
        assert image.json()["media_type"] == "image"
        assert image.json()["media"]
        image_id = image.json()["id"]

        shared = customer.post(
            messages_url,
            {
                "text": "Interested in this one",
                "shared_content_type": "product",
                "shared_object_id": b1["product_id"],
            },
            format="json",
        )
        assert shared.status_code == 201, shared.content
        card = shared.json()["shared_content"]
        assert card["content_type"] == "product"
        assert card["object_id"] == b1["product_id"]
        assert card["available"] is True
        assert card["business_id"] == b1["business_id"]
        assert card["business_name"] == "Zephyr Atelier"
        assert card["preview"]["preview_text"] == "Zephyr Jacket"

        bad_product = customer.post(
            messages_url,
            {"shared_content_type": "product", "shared_object_id": 999999},
            format="json",
        )
        assert bad_product.status_code == 404
        both = customer.post(
            messages_url,
            {
                "media": _png("x.png"),
                "shared_content_type": "product",
                "shared_object_id": b1["product_id"],
            },
            format="multipart",
        )
        assert both.status_code == 400

        # ---- STEP 10a: the Business opens the app (fetch-on-open) ---------------
        business = b1["client"]
        conversations = _results(business.get("/api/v1/conversations/"))
        assert [c["id"] for c in conversations] == [conversation_id]
        listed = conversations[0]
        assert listed["other_participant"]["display_name"] == "S3 Customer"
        assert listed["unread_count"] == 3
        assert listed["last_message"]["shared_content_type"] == "product"
        assert listed["last_message"]["status"] == "sent"

        history = _results(business.get(messages_url))
        assert [m["id"] for m in history] == [
            shared.json()["id"],
            image_id,
            text_id,
        ]

        since = business.get(f"{messages_url}?since={text_id}")
        assert since.status_code == 200
        assert [m["id"] for m in since.json()] == [image_id, shared.json()["id"]]

        # Opening / fetching alone never changes delivery status: only a live
        # recipient acknowledgment does (covered by the WebSocket test below).
        assert {m["status"] for m in history} == {"sent"}

        # Participants only: an outsider can neither read nor write.
        outsider = User.objects.create_user(
            username="p094-s3-outsider",
            email="p094-s3-outsider@example.com",
            password=PASSWORD,
            account_type=User.ACCOUNT_TYPE_CUSTOMER,
        )
        stranger = _forced_client(outsider)
        assert stranger.get(messages_url).status_code == 403
        assert stranger.post(messages_url, {"text": "hi"}, format="json").status_code == 403

        # ---- STEP 10b: offline push decision (Business not connected) -----------
        assert offline_notify.delay.call_count == 3
        assert [c.args[0] for c in offline_notify.delay.call_args_list] == [
            text_id,
            image_id,
            shared.json()["id"],
        ]

        # A connected Business (online presence key) must NOT get a push.
        cache.set(presence_cache_key(business_user.id), True, 60)
        online = customer.post(messages_url, {"text": "ping"}, format="json")
        assert online.status_code == 201
        assert offline_notify.delay.call_count == 3
        cache.delete(presence_cache_key(business_user.id))

        # The task body hands a well-formed event to the notification
        # orchestrator (chat -> notifications seam).
        dispatch_delay.reset_mock()
        offline_task(image_id)
        dispatch_delay.assert_called_once_with(
            recipient_id=business_user.id,
            notification_type="chat_message",
            title="New message",
            body="Sent a photo",
            deep_link_type="chat_thread",
            target_id=conversation_id,
        )


async def _ws_connect(conversation_id, user):
    token = str(AccessToken.for_user(user))
    communicator = WebsocketCommunicator(
        application, f"/ws/conversations/{conversation_id}/?token={token}"
    )
    connected, _ = await communicator.connect()
    assert connected is True
    return communicator


@database_sync_to_async
def _make_chat_actors():
    category = Category.objects.create(name="Async Fashion")
    business_user = User.objects.create_user(
        username="p094-ws-biz",
        email="p094-ws-biz@example.com",
        password=PASSWORD,
        account_type=User.ACCOUNT_TYPE_BUSINESS,
    )
    business = BusinessProfile.objects.create(
        user=business_user,
        business_name="Async Atelier",
        business_type="trader",
        country="Egypt",
        city="Cairo",
        category=category,
    )
    customer_user = User.objects.create_user(
        username="p094-ws-customer",
        email="p094-ws-customer@example.com",
        password=PASSWORD,
        account_type=User.ACCOUNT_TYPE_CUSTOMER,
    )
    return business_user, customer_user, business.id


@database_sync_to_async
def _message_status(message_id):
    return Message.objects.get(pk=message_id).status


class TestPhase17Steps9To10Live:
    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_live_delivery_and_status_progression(self, offline_notify):
        business_user, customer_user, business_id = await _make_chat_actors()
        customer = _forced_client(customer_user)
        business = _forced_client(business_user)

        start = await sync_to_async(customer.post)(
            "/api/v1/conversations/start/", {"business_id": business_id}, format="json"
        )
        assert start.status_code == 201, start.content
        conversation_id = start.json()["id"]
        url = f"/api/v1/conversations/{conversation_id}/messages/"

        business_ws = await _ws_connect(conversation_id, business_user)
        customer_ws = await _ws_connect(conversation_id, customer_user)

        # ---- the Business receives the message live ---------------------------
        sent = await sync_to_async(customer.post)(url, {"text": "Live hello"}, format="json")
        assert sent.status_code == 201, sent.content
        message_id = sent.json()["id"]

        live = json.loads(await business_ws.receive_from())
        assert live["id"] == message_id
        assert live["text"] == "Live hello"
        assert live["status"] == "sent"
        echoed = json.loads(await customer_ws.receive_from())
        assert echoed["id"] == message_id
        # The recipient is connected, so no offline push is queued.
        offline_notify.delay.assert_not_called()

        # ---- sent -> delivered (recipient acknowledges) ------------------------
        await business_ws.send_to(
            text_data=json.dumps({"type": "mark_delivered", "message_id": message_id})
        )
        update = {"message_id": message_id, "status": "delivered"}
        assert json.loads(await customer_ws.receive_from()) == update
        assert json.loads(await business_ws.receive_from()) == update
        assert await _message_status(message_id) == "delivered"

        customer_view = _results(await sync_to_async(customer.get)("/api/v1/conversations/"))
        assert customer_view[0]["last_message"]["status"] == "delivered"
        business_view = _results(await sync_to_async(business.get)("/api/v1/conversations/"))
        assert business_view[0]["unread_count"] == 1  # delivered, not yet read

        # ---- delivered -> read --------------------------------------------------
        await business_ws.send_to(
            text_data=json.dumps({"type": "mark_read", "message_id": message_id})
        )
        update = {"message_id": message_id, "status": "read"}
        assert json.loads(await customer_ws.receive_from()) == update
        assert json.loads(await business_ws.receive_from()) == update
        assert await _message_status(message_id) == "read"
        business_view = _results(await sync_to_async(business.get)("/api/v1/conversations/"))
        assert business_view[0]["unread_count"] == 0

        # ---- only the RECIPIENT may acknowledge (F-7) ----------------------------
        second = await sync_to_async(customer.post)(url, {"text": "Second"}, format="json")
        second_id = second.json()["id"]
        await business_ws.receive_from()
        await customer_ws.receive_from()
        await customer_ws.send_to(
            text_data=json.dumps({"type": "mark_read", "message_id": second_id})
        )
        assert await business_ws.receive_nothing(timeout=0.5) is True, (
            "SEAM GAP (F-7): the SENDER acknowledged their own message and the "
            "server broadcast a status update"
        )
        assert await _message_status(second_id) == "sent"

        # ---- Business goes offline: the next message queues an offline push ------
        await business_ws.disconnect()
        third = await sync_to_async(customer.post)(url, {"text": "Third"}, format="json")
        assert third.status_code == 201
        offline_notify.delay.assert_called_once_with(third.json()["id"])

        await customer_ws.disconnect()