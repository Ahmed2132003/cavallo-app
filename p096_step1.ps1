# =====================================================================
# P-096 / STEP 1 of 3  -  IDOR + permission sweep:
#   businesses, products, moderation, content, stories
#
# Run from: D:\Cavallo\scd-backend   (Windows PowerShell)
# Creates 6 NEW files (no existing file is modified in this step):
#   core\tests\sweep_helpers.py
#   businesses\tests\test_permission_sweep.py
#   products\tests\test_permission_sweep.py
#   moderation\tests\test_permission_sweep.py
#   content\tests\test_permission_sweep.py
#   stories\tests\test_permission_sweep.py
# No production code is touched. No migrations.
# =====================================================================

$ErrorActionPreference = "Stop"
$root = (Get-Location).Path

if (-not (Test-Path (Join-Path $root "manage.py"))) {
    throw "manage.py not found. cd D:\Cavallo\scd-backend first."
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-RepoFile([string]$Relative, [string]$Content) {
    $full = Join-Path $root $Relative
    $dir = Split-Path $full -Parent
    if (-not (Test-Path $dir)) { throw "Missing directory: $dir" }
    # Repo uses CRLF line endings (P-094 note 6): normalise to CRLF.
    $text = ($Content -replace "`r`n", "`n") -replace "`n", "`r`n"
    $text = $text.TrimEnd() + "`r`n"
    if (Test-Path $full) { Write-Host "OVERWRITE  $Relative" -ForegroundColor Yellow }
    else { Write-Host "CREATE     $Relative" -ForegroundColor Green }
    [System.IO.File]::WriteAllText($full, $text, $utf8NoBom)
}

# ---------------------------------------------------------------------
# 1) core/tests/sweep_helpers.py
# ---------------------------------------------------------------------
$sweepHelpers = @'
"""
Shared assertions for the P-096 permission / IDOR sweep.

Every negative-path sweep test asserts the P-012 error envelope, not
just the status code, so a 401/403 that accidentally carries a 500-ish
or ad-hoc body fails the sweep:

    {"error": {"code": "...", "message": "...", "fields": {}}}
"""


def assert_error_envelope(response, status_code, code):
    assert response.status_code == status_code, (
        f"expected HTTP {status_code}, got {response.status_code}: "
        f"{response.content[:300]!r}"
    )
    body = response.json()
    assert list(body.keys()) == ["error"], body
    error = body["error"]
    assert error["code"] == code, error
    assert isinstance(error["message"], str) and error["message"], error
    assert error["fields"] == {}, error


def assert_unauthenticated(response):
    assert_error_envelope(response, 401, "AUTHENTICATION_FAILED")


def assert_forbidden(response):
    assert_error_envelope(response, 403, "PERMISSION_DENIED")


def assert_not_found(response):
    assert_error_envelope(response, 404, "NOT_FOUND")
'@
Write-RepoFile "core\tests\sweep_helpers.py" $sweepHelpers

# ---------------------------------------------------------------------
# 2) businesses/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$businessesSweep = @'
"""
P-096 (step 1) permission / IDOR sweep for the businesses app.

Endpoints: /businesses/me/ (GET, POST, PATCH), /customers/me/ (GET,
POST, PATCH), /businesses/{id}/ (public GET).

Category 1 (401 + envelope), category 2 (no cross-user change; every
profile endpoint resolves from request.user, never from a body id) and
the owner-can't-self-grant-privilege case (the businesses app's only
"capability-gated" surface: verified / featured / follower_count are
Admin-controlled and must be read-only here).

Observation (not changed here): the account-type guard on POST
/businesses/me/ and /customers/me/ answers 400 VALIDATION_ERROR, not
403. That is the contract pinned by P-026's tests; this sweep pins it
too, with the envelope.
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from businesses.models import BusinessProfile, CustomerProfile
from core.tests.sweep_helpers import assert_unauthenticated

pytestmark = pytest.mark.django_db

_BUSINESS_PAYLOAD = {
    "business_name": "Sweep Biz",
    "business_type": BusinessProfile.BUSINESS_TYPE_TRADER,
    "country": "Egypt",
    "city": "Cairo",
}
_CUSTOMER_PAYLOAD = {
    "display_name": "Sweep Customer",
    "country": "Egypt",
    "city": "Cairo",
}


@pytest.fixture
def api_client():
    return APIClient()


def _make_user(account_type, email):
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type=account_type,
    )


def _make_business(email, name="Sweep Biz"):
    user = _make_user("business", email)
    return BusinessProfile.objects.create(
        user=user,
        business_name=name,
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )


def _call(client, method, url, payload=None):
    if method == "get":
        return client.get(url)
    return getattr(client, method)(url, payload or {}, format="json")


_AUTH_REQUIRED = [
    ("get", "businesses:business-me", None),
    ("post", "businesses:business-me", _BUSINESS_PAYLOAD),
    ("patch", "businesses:business-me", {"city": "Giza"}),
    ("get", "customers:customer-me", None),
    ("post", "customers:customer-me", _CUSTOMER_PAYLOAD),
    ("patch", "customers:customer-me", {"city": "Giza"}),
]


@pytest.mark.parametrize(
    "method,url_name,payload",
    _AUTH_REQUIRED,
    ids=[f"{m}-{n}" for m, n, _ in _AUTH_REQUIRED],
)
def test_unauthenticated_gets_401_envelope_and_creates_nothing(
    api_client, method, url_name, payload
):
    response = _call(api_client, method, reverse(url_name), payload)

    assert_unauthenticated(response)
    assert BusinessProfile.objects.count() == 0
    assert CustomerProfile.objects.count() == 0


def test_public_business_profile_needs_no_auth_and_unknown_id_is_404(api_client):
    profile = _make_business("p096-pub@example.com")

    ok = api_client.get(reverse("businesses:business-public", kwargs={"pk": profile.id}))
    missing = api_client.get(reverse("businesses:business-public", kwargs={"pk": 999999}))

    assert ok.status_code == 200
    assert missing.status_code == 404


def test_customer_account_creating_business_profile_is_rejected_with_envelope(
    api_client,
):
    user = _make_user("customer", "p096-cust-guard@example.com")
    api_client.force_authenticate(user=user)

    response = api_client.post(
        reverse("businesses:business-me"), _BUSINESS_PAYLOAD, format="json"
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert BusinessProfile.objects.count() == 0


def test_business_account_creating_customer_profile_is_rejected_with_envelope(
    api_client,
):
    user = _make_user("business", "p096-biz-guard@example.com")
    api_client.force_authenticate(user=user)

    response = api_client.post(
        reverse("customers:customer-me"), _CUSTOMER_PAYLOAD, format="json"
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert CustomerProfile.objects.count() == 0


def test_business_patch_with_spoofed_ids_never_touches_another_business(
    api_client,
):
    victim = _make_business("p096-victim@example.com", name="Victim Biz")
    attacker = _make_business("p096-attacker@example.com", name="Attacker Biz")
    api_client.force_authenticate(user=attacker.user)

    response = api_client.patch(
        reverse("businesses:business-me"),
        {"id": victim.id, "user": victim.user_id, "business_name": "Renamed"},
        format="json",
    )

    assert response.status_code == 200
    victim.refresh_from_db()
    attacker.refresh_from_db()
    assert victim.business_name == "Victim Biz"
    assert victim.user_id != attacker.user_id
    assert attacker.business_name == "Renamed"


def test_owner_cannot_self_grant_featured_verified_or_followers(api_client):
    profile = _make_business("p096-priv@example.com")
    api_client.force_authenticate(user=profile.user)

    response = api_client.patch(
        reverse("businesses:business-me"),
        {
            "is_featured": True,
            "is_verified": True,
            "follower_count": 9999,
            "city": "Giza",
        },
        format="json",
    )

    assert response.status_code == 200
    profile.refresh_from_db()
    assert profile.is_featured is False
    assert profile.follower_count == 0
    assert profile.city == "Giza"
    assert response.json()["is_verified"] is False


def test_customer_me_resolves_from_request_user_never_from_another_customer(
    api_client,
):
    first = _make_user("customer", "p096-c1@example.com")
    second = _make_user("customer", "p096-c2@example.com")
    CustomerProfile.objects.create(
        user=first, display_name="First", country="Egypt", city="Cairo"
    )
    CustomerProfile.objects.create(
        user=second, display_name="Second", country="Egypt", city="Cairo"
    )
    api_client.force_authenticate(user=second)

    response = api_client.get(reverse("customers:customer-me"))
    patched = api_client.patch(
        reverse("customers:customer-me"),
        {"display_name": "Second Renamed", "user": first.id},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "Second"
    assert patched.status_code == 200
    assert CustomerProfile.objects.get(user=first).display_name == "First"
    assert CustomerProfile.objects.get(user=second).display_name == "Second Renamed"
'@
Write-RepoFile "businesses\tests\test_permission_sweep.py" $businessesSweep

# ---------------------------------------------------------------------
# 3) products/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$productsSweep = @'
"""
P-096 (step 1) permission / IDOR sweep for the products app.

Covers the 5 write routes (PATCH/DELETE product, POST variant, PATCH/
DELETE variant) against: no credentials (401), a different business
(403), and a customer account (403) - each time asserting the P-012
envelope AND that nothing in the database changed (follow-up query).
Also the cross-product variant id (404, not 403) and product create by
an account with no business profile.
"""

from types import SimpleNamespace

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from businesses.models import BusinessProfile
from categories.models import Category
from core.tests.sweep_helpers import (
    assert_forbidden,
    assert_not_found,
    assert_unauthenticated,
)
from products.models import Product, ProductVariant

pytestmark = pytest.mark.django_db


def _make_user(account_type, email):
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type=account_type,
    )


def _make_business_profile(email):
    return BusinessProfile.objects.create(
        user=_make_user("business", email),
        business_name=f"Business {email}",
        business_type=BusinessProfile.BUSINESS_TYPE_TRADER,
        country="Egypt",
        city="Cairo",
    )


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def catalog():
    owner = _make_business_profile("p096-prod-owner@example.com")
    category = Category.objects.create(name="Sweep Fashion")
    product = Product.objects.create(
        business=owner,
        category=category,
        name="Original Name",
        description="A product.",
        price="199.99",
        currency=Product.CURRENCY_EGP,
    )
    variant = ProductVariant.objects.create(
        product=product, name="Size", value="Original"
    )
    return SimpleNamespace(
        owner=owner, category=category, product=product, variant=variant
    )


def _product_url(pk):
    return reverse("products:product-detail", kwargs={"pk": pk})


def _variant_create_url(product_id):
    return reverse("products:product-variant-create", kwargs={"product_pk": product_id})


def _variant_url(product_id, variant_id):
    return reverse(
        "products:product-variant-detail",
        kwargs={"product_pk": product_id, "pk": variant_id},
    )


def _write_requests(c):
    return {
        "patch-product": ("patch", _product_url(c.product.id), {"name": "Hacked"}),
        "delete-product": ("delete", _product_url(c.product.id), None),
        "create-variant": (
            "post",
            _variant_create_url(c.product.id),
            {"name": "Color", "value": "Red"},
        ),
        "patch-variant": (
            "patch",
            _variant_url(c.product.id, c.variant.id),
            {"value": "Hacked"},
        ),
        "delete-variant": ("delete", _variant_url(c.product.id, c.variant.id), None),
    }


WRITE_LABELS = [
    "patch-product",
    "delete-product",
    "create-variant",
    "patch-variant",
    "delete-variant",
]


def _send(client, method, url, payload):
    if method == "delete":
        return client.delete(url)
    return getattr(client, method)(url, payload, format="json")


def _assert_catalog_untouched(c):
    product = Product.all_objects.get(pk=c.product.pk)
    assert product.name == "Original Name"
    assert product.is_deleted is False
    variants = ProductVariant.objects.filter(product_id=c.product.pk)
    assert variants.count() == 1
    assert variants.get().value == "Original"


@pytest.mark.parametrize("label", WRITE_LABELS)
def test_unauthenticated_write_gets_401_and_changes_nothing(
    api_client, catalog, label
):
    method, url, payload = _write_requests(catalog)[label]

    response = _send(api_client, method, url, payload)

    assert_unauthenticated(response)
    _assert_catalog_untouched(catalog)


@pytest.mark.parametrize("actor", ["other_business", "customer"])
@pytest.mark.parametrize("label", WRITE_LABELS)
def test_non_owner_write_gets_403_and_changes_nothing(
    api_client, catalog, label, actor
):
    if actor == "other_business":
        user = _make_business_profile("p096-prod-attacker@example.com").user
    else:
        user = _make_user("customer", "p096-prod-customer@example.com")
    api_client.force_authenticate(user=user)
    method, url, payload = _write_requests(catalog)[label]

    response = _send(api_client, method, url, payload)

    assert_forbidden(response)
    _assert_catalog_untouched(catalog)


def test_unauthenticated_create_and_own_list_get_401_and_create_nothing(api_client):
    category = Category.objects.create(name="Sweep Create")
    payload = {
        "category": category.id,
        "name": "Ghost",
        "description": "x",
        "price": "10.00",
        "currency": Product.CURRENCY_EGP,
    }

    created = api_client.post(reverse("products:product-list-create"), payload, format="json")
    listed = api_client.get(reverse("products:product-list-create"))

    assert_unauthenticated(created)
    assert_unauthenticated(listed)
    assert Product.all_objects.count() == 0


def test_customer_without_business_profile_cannot_create_product(api_client):
    category = Category.objects.create(name="Sweep Customer Create")
    api_client.force_authenticate(user=_make_user("customer", "p096-nobiz@example.com"))

    response = api_client.post(
        reverse("products:product-list-create"),
        {
            "category": category.id,
            "name": "Ghost",
            "description": "x",
            "price": "10.00",
            "currency": Product.CURRENCY_EGP,
        },
        format="json",
    )

    assert_forbidden(response)
    assert Product.all_objects.count() == 0


def test_variant_id_of_another_product_is_404_with_envelope_and_untouched(
    api_client, catalog
):
    other = _make_business_profile("p096-prod-other@example.com")
    other_product = Product.objects.create(
        business=other,
        category=catalog.category,
        name="Other Product",
        description="x",
        price="5.00",
        currency=Product.CURRENCY_EGP,
    )
    api_client.force_authenticate(user=other.user)

    patched = api_client.patch(
        _variant_url(other_product.id, catalog.variant.id),
        {"value": "Hacked"},
        format="json",
    )
    deleted = api_client.delete(_variant_url(other_product.id, catalog.variant.id))

    assert_not_found(patched)
    assert_not_found(deleted)
    _assert_catalog_untouched(catalog)


def test_public_reads_stay_open_to_anonymous_users(api_client, catalog):
    detail = api_client.get(_product_url(catalog.product.id))
    variant = api_client.get(_variant_url(catalog.product.id, catalog.variant.id))
    public_list = api_client.get(reverse("products:product-public-list"))

    assert detail.status_code == 200
    assert variant.status_code == 200
    assert public_list.status_code == 200
'@
Write-RepoFile "products\tests\test_permission_sweep.py" $productsSweep

# ---------------------------------------------------------------------
# 4) moderation/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$moderationSweep = @'
"""
P-096 (step 1) permission sweep for the moderation app.

test_api.py already proves: anonymous -> 401 envelope, customer -> 403
envelope + item still pending. This sweep adds what it does not:

  * a garbage Bearer token is 401 (not 500, not 200);
  * a Business-account user (e.g. the content's own owner) and a
    Django `is_staff` user with no capability are 403 - authorization
    is permission-based (Architecture Section 4), not is_staff-based;
  * every denied request leaves the queue item, the content and the
    ModerationLog table unchanged (follow-up query);
  * an unprivileged caller gets 403 even for an id that does not exist,
    so the endpoints cannot be used to enumerate queue ids.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from rest_framework.test import APIClient

from core.tests.sweep_helpers import assert_forbidden, assert_unauthenticated
from moderation.models import Moderatable, ModerationLog, ModerationQueue
from moderation.tests.testapp.models import DummyContent

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def pending_item():
    content = DummyContent.objects.create()
    item = ModerationQueue.objects.get(
        content_type=ContentType.objects.get_for_model(content),
        object_id=content.pk,
    )
    return content, item


def _requests(item_pk):
    return {
        "list": ("get", reverse("moderation-queue-list"), None),
        "approve": (
            "post",
            reverse("moderation-queue-approve", args=[item_pk]),
            None,
        ),
        "reject": (
            "post",
            reverse("moderation-queue-reject", args=[item_pk]),
            {"reason": "not allowed"},
        ),
    }


LABELS = ["list", "approve", "reject"]


def _send(client, method, url, payload):
    if method == "get":
        return client.get(url)
    return client.post(url, payload or {}, format="json")


def _assert_nothing_changed(content, item, logs_before):
    item.refresh_from_db()
    content.refresh_from_db()
    assert item.status == ModerationQueue.Status.PENDING
    assert content.status == Moderatable.Status.PENDING_REVIEW
    assert ModerationLog.objects.count() == logs_before


@pytest.mark.parametrize("label", LABELS)
def test_unauthenticated_gets_401_and_changes_nothing(client, pending_item, label):
    content, item = pending_item
    logs_before = ModerationLog.objects.count()
    method, url, payload = _requests(item.pk)[label]

    response = _send(client, method, url, payload)

    assert_unauthenticated(response)
    _assert_nothing_changed(content, item, logs_before)


@pytest.mark.parametrize("label", LABELS)
def test_garbage_bearer_token_gets_401_and_changes_nothing(
    client, pending_item, label
):
    content, item = pending_item
    logs_before = ModerationLog.objects.count()
    client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    method, url, payload = _requests(item.pk)[label]

    response = _send(client, method, url, payload)

    assert_unauthenticated(response)
    _assert_nothing_changed(content, item, logs_before)


@pytest.mark.parametrize("actor", ["business_account", "staff_without_capability"])
@pytest.mark.parametrize("label", LABELS)
def test_user_without_capability_gets_403_and_changes_nothing(
    client, pending_item, label, actor
):
    content, item = pending_item
    logs_before = ModerationLog.objects.count()
    if actor == "business_account":
        user = User.objects.create_user(
            username="p096-biz-mod",
            password="pw12345",
            account_type=User.ACCOUNT_TYPE_BUSINESS,
        )
    else:
        user = User.objects.create_user(
            username="p096-staff-mod",
            password="pw12345",
            account_type=User.ACCOUNT_TYPE_CUSTOMER,
            is_staff=True,
        )
    client.force_authenticate(user)
    method, url, payload = _requests(item.pk)[label]

    response = _send(client, method, url, payload)

    assert_forbidden(response)
    _assert_nothing_changed(content, item, logs_before)


@pytest.mark.parametrize("label", ["approve", "reject"])
def test_unprivileged_caller_gets_403_not_404_for_unknown_id(client, label):
    user = User.objects.create_user(
        username="p096-enum",
        password="pw12345",
        account_type=User.ACCOUNT_TYPE_CUSTOMER,
    )
    client.force_authenticate(user)
    method, url, payload = _requests(999999)[label]

    response = _send(client, method, url, payload)

    assert_forbidden(response)
'@
Write-RepoFile "moderation\tests\test_permission_sweep.py" $moderationSweep

# ---------------------------------------------------------------------
# 5) content/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$contentSweep = @'
"""
P-096 (step 1) permission / IDOR sweep for the content app (Posts and
Reels share one parametrised suite, since their views are
byte-for-byte the same ownership pattern).

For both kinds: anonymous write -> 401, a different business or a
customer account -> 403, an authenticated write on an unknown id -> 404;
every denied request asserts the P-012 envelope AND re-reads the row to
prove nothing changed (caption, soft-delete flag, row count).
"""

import pytest
from rest_framework.test import APIClient

from accounts.models import User
from businesses.services import create_business_profile
from content.models import Post, Reel
from content.tests.test_api import _make_video_upload
from core.tests.sweep_helpers import (
    assert_forbidden,
    assert_not_found,
    assert_unauthenticated,
)

pytestmark = pytest.mark.django_db

KINDS = ["post", "reel"]


def _make_business_user(email, name):
    user = User.objects.create_user(
        username=email, email=email, password="testpass123", account_type="business"
    )
    business = create_business_profile(
        user=user,
        business_name=name,
        business_type="trader",
        country="EG",
        city="Ismailia",
    )
    return user, business


def _make_customer(email):
    return User.objects.create_user(
        username=email, email=email, password="testpass123", account_type="customer"
    )


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def world():
    owner, business = _make_business_user("p096-content-owner@example.com", "Owner")
    post = Post.objects.create(business=business, caption="original caption")
    reel = Reel.objects.create(
        business=business, caption="original caption", video=_make_video_upload()
    )
    return {"owner": owner, "business": business, "post": post, "reel": reel}


def _model(kind):
    return Post if kind == "post" else Reel


def _list_url(kind):
    return f"/api/v1/{kind}s/"


def _detail_url(kind, pk):
    return f"/api/v1/{kind}s/{pk}/"


def _count(kind):
    return _model(kind)._base_manager.count()


def _assert_untouched(obj):
    obj.refresh_from_db()
    assert obj.caption == "original caption"
    assert obj.is_deleted is False


def _create(client, kind):
    if kind == "post":
        return client.post(_list_url(kind), {"caption": "ghost"}, format="json")
    return client.post(
        _list_url(kind),
        {"caption": "ghost", "video": _make_video_upload()},
        format="multipart",
    )


@pytest.mark.parametrize("method", ["patch", "delete"])
@pytest.mark.parametrize("kind", KINDS)
def test_unauthenticated_write_gets_401_and_changes_nothing(
    api_client, world, kind, method
):
    obj = world[kind]
    url = _detail_url(kind, obj.pk)

    if method == "patch":
        response = api_client.patch(url, {"caption": "hijacked"}, format="json")
    else:
        response = api_client.delete(url)

    assert_unauthenticated(response)
    _assert_untouched(obj)


@pytest.mark.parametrize("kind", KINDS)
def test_unauthenticated_create_and_own_list_get_401_and_create_nothing(
    api_client, world, kind
):
    before = _count(kind)

    created = _create(api_client, kind)
    listed = api_client.get(_list_url(kind))

    assert_unauthenticated(created)
    assert_unauthenticated(listed)
    assert _count(kind) == before


@pytest.mark.parametrize("actor", ["other_business", "customer"])
@pytest.mark.parametrize("method", ["patch", "delete"])
@pytest.mark.parametrize("kind", KINDS)
def test_non_owner_write_gets_403_and_changes_nothing(
    api_client, world, kind, method, actor
):
    if actor == "other_business":
        user, _ = _make_business_user("p096-content-other@example.com", "Other")
    else:
        user = _make_customer("p096-content-customer@example.com")
    api_client.force_authenticate(user)
    obj = world[kind]
    url = _detail_url(kind, obj.pk)

    if method == "patch":
        response = api_client.patch(url, {"caption": "hijacked"}, format="json")
    else:
        response = api_client.delete(url)

    assert_forbidden(response)
    _assert_untouched(obj)


@pytest.mark.parametrize("kind", KINDS)
def test_customer_without_business_profile_cannot_create(api_client, world, kind):
    api_client.force_authenticate(_make_customer("p096-content-nobiz@example.com"))
    before = _count(kind)

    response = _create(api_client, kind)

    assert_forbidden(response)
    assert _count(kind) == before


@pytest.mark.parametrize("method", ["patch", "delete"])
@pytest.mark.parametrize("kind", KINDS)
def test_authenticated_write_on_unknown_id_is_404_with_envelope(
    api_client, world, kind, method
):
    api_client.force_authenticate(world["owner"])
    url = _detail_url(kind, 999999)

    if method == "patch":
        response = api_client.patch(url, {"caption": "x"}, format="json")
    else:
        response = api_client.delete(url)

    assert_not_found(response)


@pytest.mark.parametrize("kind", KINDS)
def test_public_reads_stay_open_to_anonymous_users(api_client, world, kind):
    detail = api_client.get(_detail_url(kind, world[kind].pk))
    public_list = api_client.get(f"{_list_url(kind)}public/")

    assert detail.status_code == 200
    assert public_list.status_code == 200
'@
Write-RepoFile "content\tests\test_permission_sweep.py" $contentSweep

# ---------------------------------------------------------------------
# 6) stories/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$storiesSweep = @'
"""
P-096 (step 1) permission / IDOR sweep for the stories app.

Routes: POST/GET /stories/, GET /stories/public/, POST /stories/{id}/
view/, GET /stories/{id}/view-count/. There is deliberately NO
PATCH/DELETE-by-id route; the last test pins that, so one cannot appear
later without an ownership check and a sweep entry.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from accounts.models import User
from businesses.services import create_business_profile
from core.tests.sweep_helpers import (
    assert_forbidden,
    assert_not_found,
    assert_unauthenticated,
)
from core.tests.test_media import _VALID_PNG_BYTES
from stories.models import Story, StoryView

pytestmark = pytest.mark.django_db


def _png(name="s.png"):
    return SimpleUploadedFile(name, _VALID_PNG_BYTES, content_type="image/png")


def _make_business_user(email, name):
    user = User.objects.create_user(
        username=email, email=email, password="testpass123", account_type="business"
    )
    business = create_business_profile(
        user=user,
        business_name=name,
        business_type="trader",
        country="EG",
        city="Ismailia",
    )
    return user, business


def _make_customer(email):
    return User.objects.create_user(
        username=email, email=email, password="testpass123", account_type="customer"
    )


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def world():
    owner, business = _make_business_user("p096-story-owner@example.com", "Owner")
    story = Story.objects.create(business=business, media=_png("owner.png"))
    return {"owner": owner, "business": business, "story": story}


def test_unauthenticated_gets_401_and_creates_nothing(api_client, world):
    story_id = world["story"].id
    stories_before = Story._base_manager.count()

    created = api_client.post("/api/v1/stories/", {"media": _png()}, format="multipart")
    listed = api_client.get("/api/v1/stories/")
    viewed = api_client.post(f"/api/v1/stories/{story_id}/view/")
    counted = api_client.get(f"/api/v1/stories/{story_id}/view-count/")

    for response in (created, listed, viewed, counted):
        assert_unauthenticated(response)
    assert Story._base_manager.count() == stories_before
    assert StoryView.objects.count() == 0


def test_customer_without_business_profile_cannot_create_story(api_client, world):
    api_client.force_authenticate(_make_customer("p096-story-nobiz@example.com"))
    before = Story._base_manager.count()

    response = api_client.post(
        "/api/v1/stories/", {"media": _png()}, format="multipart"
    )

    assert_forbidden(response)
    assert Story._base_manager.count() == before


@pytest.mark.parametrize("actor", ["other_business", "customer"])
def test_non_owner_view_count_is_403_and_leaks_no_count(api_client, world, actor):
    if actor == "other_business":
        user, _ = _make_business_user("p096-story-other@example.com", "Other")
    else:
        user = _make_customer("p096-story-customer@example.com")
    StoryView.objects.create(story=world["story"], viewer=_make_customer("v@example.com"))
    api_client.force_authenticate(user)
    views_before = StoryView.objects.count()

    response = api_client.get(f"/api/v1/stories/{world['story'].id}/view-count/")

    assert_forbidden(response)
    assert "view_count" not in response.json()
    assert StoryView.objects.count() == views_before


def test_unknown_story_is_404_with_envelope_for_view_and_view_count(
    api_client, world
):
    api_client.force_authenticate(_make_customer("p096-story-404@example.com"))

    viewed = api_client.post("/api/v1/stories/999999/view/")
    counted = api_client.get("/api/v1/stories/999999/view-count/")

    assert_not_found(viewed)
    assert_not_found(counted)
    assert StoryView.objects.count() == 0


def test_public_story_list_stays_open_to_anonymous_users(api_client, world):
    assert api_client.get("/api/v1/stories/public/").status_code == 200


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_no_write_by_id_route_exists_so_nothing_can_be_changed(
    api_client, world, method
):
    api_client.force_authenticate(world["owner"])
    story = world["story"]

    response = getattr(api_client, method)(f"/api/v1/stories/{story.id}/")

    assert response.status_code in (404, 405)
    story.refresh_from_db()
    assert story.is_deleted is False
'@
Write-RepoFile "stories\tests\test_permission_sweep.py" $storiesSweep

Write-Host ""
Write-Host "Done: 6 files written. No existing file was modified." -ForegroundColor Cyan
Write-Host "Next: format + run the checks from the STEP 1 message."