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

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

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