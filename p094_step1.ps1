# p094_step1.ps1  --  run from D:\Cavallo\scd-backend
# P-094 STEP 1 of 3: preconditions audit + automated integration test for walkthrough steps 1-4
# + INTEGRATION_TEST_REPORT_PHASE17.md skeleton. Idempotent: existing files are skipped, never overwritten.
$ErrorActionPreference = 'Stop'

if (-not (Test-Path .\manage.py) -or -not (Test-Path .\core\tests)) {
    throw 'Run this script from D:\Cavallo\scd-backend (manage.py / core\tests not found).'
}

$utf8 = New-Object System.Text.UTF8Encoding($false)

function ConvertTo-Crlf([string]$s) {
    return ($s -replace "`r?`n", "`r`n")
}

function New-ProjectFile {
    param([string]$Path, [string]$Content)
    $full = Join-Path (Get-Location).Path $Path
    if (Test-Path $full) {
        Write-Host "SKIP (file exists): $Path"
        return
    }
    [IO.File]::WriteAllText($full, (ConvertTo-Crlf $Content), $utf8)
    Write-Host "CREATED: $Path"
}

# ------------------------------------------------------------ 1. preconditions audit (read-only)
Write-Host '== Preconditions audit (repository is ground truth, not PROJECT_PROGRESS.md) =='
$required = @(
    @{ File = 'config\urls.py'; Pattern = 'api/v1/auth/' },
    @{ File = 'config\urls.py'; Pattern = 'api/v1/businesses/' },
    @{ File = 'config\urls.py'; Pattern = 'api/v1/products/' },
    @{ File = 'config\urls.py'; Pattern = 'api/v1/posts/' },
    @{ File = 'config\urls.py'; Pattern = 'api/v1/stories/' },
    @{ File = 'config\urls.py'; Pattern = 'api/v1/moderation/' },
    @{ File = 'config\urls.py'; Pattern = 'api/v1/feed/' },
    @{ File = 'config\urls.py'; Pattern = 'api/v1/search/' },
    @{ File = 'config\urls.py'; Pattern = 'api/v1/conversations/' },
    @{ File = 'config\urls.py'; Pattern = 'api/v1/notifications/' },
    @{ File = 'config\urls.py'; Pattern = 'ratings.urls' },
    @{ File = 'config\urls.py'; Pattern = 'api/v1/follow' },
    @{ File = 'monetization\models.py'; Pattern = 'class ' },
    @{ File = 'chat\consumers.py'; Pattern = 'class ' },
    @{ File = 'notifications\tasks.py'; Pattern = 'dispatch_notification' }
)
$missing = @()
foreach ($r in $required) {
    $full = Join-Path (Get-Location).Path $r.File
    if (-not (Test-Path $full)) { $missing += "$($r.File) (file missing)"; continue }
    $hit = Select-String -Path $full -Pattern ([regex]::Escape($r.Pattern)) -Quiet
    if ($hit) { Write-Host "OK      $($r.File)  [$($r.Pattern)]" }
    else      { Write-Host "MISSING $($r.File)  [$($r.Pattern)]"; $missing += "$($r.File) [$($r.Pattern)]" }
}
# 'api/v1/follow' is expected to be MISSING: Follow lives under api/v1/businesses/<id>/follow/ (social.urls), not its own prefix.
$missing = $missing | Where-Object { $_ -notmatch 'api/v1/follow' }
if ($missing.Count -gt 0) {
    Write-Host ''
    Write-Host 'Missing pieces found:' -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    throw 'Preconditions audit failed - report this output back before continuing.'
}
Write-Host 'Audit passed.' -ForegroundColor Green
Write-Host ''

# ------------------------------------------------------------ 2. automated integration test (steps 1-4)
New-ProjectFile -Path 'core\tests\test_integration_phase17.py' -Content @'
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
- "Admin verifies the business" is done with an ORM update of
  User.is_business_verified because verification is a Django Admin action
  (no public API exists for it). The manual walkthrough covers the real
  Django Admin toggle.
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
        business_user = User.objects.get(email="p094-biz@example.com")
        User.objects.filter(pk=business_user.pk).update(is_business_verified=True)
        public_profile = APIClient().get(f"/api/v1/businesses/{business_id}/")
        assert public_profile.status_code == 200
        assert public_profile.json()["is_verified"] is True, (
            "SEAM GAP: is_business_verified toggled on User but not reflected "
            "on the public BusinessProfile representation"
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
'@

# ------------------------------------------------------------ 3. report skeleton
New-ProjectFile -Path 'INTEGRATION_TEST_REPORT_PHASE17.md' -Content @'
# INTEGRATION_TEST_REPORT_PHASE17 - Part P-094 End-to-End Integration Pass

Status: IN PROGRESS (script step 1 of 3 applied)

## 0. Preconditions (Phases 3-16)
Verified against the repository (not only PROJECT_PROGRESS.md) by `p094_step1.ps1`'s audit:
all API prefixes for auth, businesses (+follow, +ratings), products, posts, stories, moderation,
feed, search, conversations, notifications are routed in `config/urls.py`; chat consumers,
monetization models and `dispatch_notification` exist.
Open items inherited from PROJECT_PROGRESS.md Section 7 (NOT verified by this pass):
live Paymob verification and live FCM push delivery.

## 1. Walkthrough results
| # | Step | Method | Result | Notes |
|---|------|--------|--------|-------|
| 1 | Register Business, onboarding (P-028), Admin verify | automated (test_integration_phase17.py) | PENDING RUN | Admin verify is an ORM update in the test; real Django Admin toggle to be checked manually |
| 2 | Create Product, Post, Story | automated | PENDING RUN | |
| 3 | Moderator approves | automated | PENDING RUN | Products are not moderated (F-1) |
| 4 | Register Customer | automated | PENDING RUN | |
| 5 | Search (text + filter) and Discover | script step 2 | NOT STARTED | |
| 6 | Follow, Home feed hybrid algorithm on fresh fetch | script step 2 | NOT STARTED | |
| 7 | Like / Comment / Save / Share | script step 2 | NOT STARTED | |
| 8 | Rating + average_rating | script step 2 | NOT STARTED | |
| 9 | Chat: text, image, product share | script step 3 | NOT STARTED | |
| 10 | Real-time delivery + status progression | script step 3 | NOT STARTED | |
| 11 | Notifications + deep links | script step 3 | NOT STARTED | |
| 12 | Featured ranking in Search and Feed | script step 3 | NOT STARTED | |

## 2. Findings
- F-1 (plan vs code, not a bug): P-094 step 3 says a Moderator approves Product, Post and Story,
  but `products.Product` is not a `moderation.Moderatable`; products are visible immediately
  (`is_active`) and only Admin can hide/remove them (matches the product deck, Admin Dashboard
  "Products: Hide / Remove"). Post and Story are moderated.

## 3. Bugs found and fixed
None yet.

## 4. Open follow-up items
None yet.
'@

Write-Host ''
Write-Host 'STEP 1 files written. Now run (from D:\Cavallo\scd-backend):' -ForegroundColor Cyan
Write-Host '  docker compose exec web pytest core/tests/test_integration_phase17.py -v' -ForegroundColor Cyan
Write-Host '  (or, if you run pytest on the host: pytest core\tests\test_integration_phase17.py -v)' -ForegroundColor Cyan