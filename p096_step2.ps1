# =====================================================================
# P-096 / STEP 2 of 3  -  IDOR + permission sweep:
#   social, reports, ratings, feed, search
#
# Run from: D:\Cavallo\scd-backend   (Windows PowerShell)
# Creates 6 NEW files (no existing file is modified, no production code):
#   core\tests\sweep_factories.py
#   social\tests\test_permission_sweep.py
#   reports\tests\test_permission_sweep.py
#   ratings\tests\test_permission_sweep.py
#   feed\tests\test_permission_sweep.py
#   search\tests\test_permission_sweep.py
# Requires step 1 (core\tests\sweep_helpers.py must already exist).
# =====================================================================

$ErrorActionPreference = "Stop"
$root = (Get-Location).Path

if (-not (Test-Path (Join-Path $root "manage.py"))) {
    throw "manage.py not found. cd D:\Cavallo\scd-backend first."
}
if (-not (Test-Path (Join-Path $root "core\tests\sweep_helpers.py"))) {
    throw "core\tests\sweep_helpers.py is missing - run p096_step1.ps1 first."
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-RepoFile([string]$Relative, [string]$Content) {
    $full = Join-Path $root $Relative
    $dir = Split-Path $full -Parent
    if (-not (Test-Path $dir)) { throw "Missing directory: $dir" }
    # Repo uses CRLF line endings: normalise to CRLF.
    $text = ($Content -replace "`r`n", "`n") -replace "`n", "`r`n"
    $text = $text.TrimEnd() + "`r`n"
    if (Test-Path $full) { Write-Host "OVERWRITE  $Relative" -ForegroundColor Yellow }
    else { Write-Host "CREATE     $Relative" -ForegroundColor Green }
    [System.IO.File]::WriteAllText($full, $text, $utf8NoBom)
}

# ---------------------------------------------------------------------
# 1) core/tests/sweep_factories.py
# ---------------------------------------------------------------------
$factories = @'
"""
Small data factories shared by the P-096 sweep tests (step 2 onwards).

Kept separate from core/tests/sweep_helpers.py (assertions only) so
each file has one job. Nothing here touches production code.
"""

import itertools

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from businesses.services import create_business_profile
from content.models import Post

User = get_user_model()

_counter = itertools.count(1)


def make_user(account_type="customer", prefix="sweep"):
    email = f"{prefix}-{account_type}-{next(_counter)}@example.com"
    return User.objects.create_user(
        username=email,
        email=email,
        password="Str0ngPass!23",
        account_type=account_type,
    )


def make_business(name="Sweep Biz"):
    return create_business_profile(
        user=make_user("business", "sweep-biz"),
        business_name=name,
        business_type="trader",
        country="Egypt",
        city="Cairo",
    )


def make_post(business, *, published=True, caption="sweep post"):
    extra = {"status": Post.Status.PUBLISHED} if published else {}
    return Post.objects.create(business=business, caption=caption, **extra)


def client_for(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def garbage_token_client():
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    return client
'@
Write-RepoFile "core\tests\sweep_factories.py" $factories

# ---------------------------------------------------------------------
# 2) social/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$socialSweep = @'
"""
P-096 (step 2) permission / IDOR sweep for the social app.

Routes: follow, like, save (+ saves/me), comment (create + list),
share. All writes are "act as request.user on a target"; the
IDOR-shaped risks are therefore (a) acting as someone else via the
body, (b) one user's undo touching another user's rows/counters, and
(c) the capability-gated visibility of auto-hidden comments.

Category 1: every route -> 401 envelope (no token and garbage token),
            and no row or counter changes.
Category 2: spoofed `user`/`follower` in the body is ignored; user B's
            unfollow/unlike/unsave never touches user A's rows or the
            counters; saves/me is always the caller's own list;
            unknown targets are 404 with the NOT_FOUND envelope.
Category 3: hidden comments are visible only to their author and to
            holders of can_moderate_content - not to the content
            owner and not to an is_staff user without the capability.

OPEN FINDING S-1 (characterised, NOT changed here): Like/Save resolve
their target with `model.objects` (any non-deleted row), while
Comment/Share/Report require a PUBLISHED target. A user can therefore
like a pending/rejected post of another business by id, bumping its
likes_count. The last test pins today's behaviour so a fix is a
deliberate, visible change.
"""

from types import SimpleNamespace

import pytest
from django.contrib.contenttypes.models import ContentType

from content.models import Post
from core.tests.sweep_factories import (
    client_for,
    garbage_token_client,
    make_business,
    make_post,
    make_user,
)
from core.tests.sweep_helpers import assert_not_found, assert_unauthenticated
from social.models import Comment, Follow, Like, Save, Share

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    business = make_business("Social Owner Biz")
    return SimpleNamespace(
        business=business, owner=business.user, post=make_post(business)
    )


def _target(w):
    return {"content_type": "post", "object_id": w.post.pk}


def _requests(w):
    follow_url = f"/api/v1/businesses/{w.business.pk}/follow/"
    target = _target(w)
    return {
        "follow": ("post", follow_url, None),
        "unfollow": ("delete", follow_url, None),
        "like": ("post", "/api/v1/likes/", target),
        "unlike": ("delete", "/api/v1/likes/", target),
        "save": ("post", "/api/v1/saves/", target),
        "unsave": ("delete", "/api/v1/saves/", target),
        "saves-me": ("get", "/api/v1/saves/me/", None),
        "comment": ("post", "/api/v1/comments/", {**target, "text": "hello"}),
        "share": ("post", "/api/v1/shares/", target),
    }


LABELS = [
    "follow",
    "unfollow",
    "like",
    "unlike",
    "save",
    "unsave",
    "saves-me",
    "comment",
    "share",
]


def _send(client, method, url, payload):
    if method == "get":
        return client.get(url)
    if method == "delete":
        return client.delete(url, payload or {}, format="json")
    return client.post(url, payload or {}, format="json")


def _row_counts():
    return (
        Follow.objects.count(),
        Like.objects.count(),
        Save.objects.count(),
        Comment.objects.count(),
        Share.objects.count(),
    )


def _counters(w):
    w.business.refresh_from_db()
    w.post.refresh_from_db()
    return (
        w.business.follower_count,
        w.post.likes_count,
        w.post.comments_count,
        w.post.shares_count,
    )


@pytest.mark.parametrize("client_factory", [client_for, garbage_token_client])
@pytest.mark.parametrize("label", LABELS)
def test_unauthenticated_gets_401_and_changes_nothing(world, label, client_factory):
    rows_before = _row_counts()
    counters_before = _counters(world)
    method, url, payload = _requests(world)[label]

    response = _send(client_factory(), method, url, payload)

    assert_unauthenticated(response)
    assert _row_counts() == rows_before
    assert _counters(world) == counters_before


def test_public_comment_list_stays_open_to_anonymous_users(world):
    response = client_for().get("/api/v1/comments/", _target(world))

    assert response.status_code == 200


def test_other_users_undo_never_touches_my_rows_or_counters(world):
    me, other = make_user(), make_user()
    mine = client_for(me)
    for label in ("follow", "like", "save"):
        method, url, payload = _requests(world)[label]
        assert _send(mine, method, url, payload).status_code == 200
    rows_before = _row_counts()
    counters_before = _counters(world)
    assert rows_before[:3] == (1, 1, 1)
    assert counters_before[:2] == (1, 1)

    theirs = client_for(other)
    for label in ("unfollow", "unlike", "unsave"):
        method, url, payload = _requests(world)[label]
        response = _send(theirs, method, url, payload)
        assert response.status_code == 200  # idempotent no-op for them

    assert _row_counts() == rows_before
    assert _counters(world) == counters_before
    me.refresh_from_db()
    other.refresh_from_db()
    assert me.following_count == 1
    assert other.following_count == 0


def test_saves_me_only_ever_lists_the_callers_own_saves(world):
    me, other = make_user(), make_user()
    other_post = make_post(world.business, caption="other post")
    client_for(me).post("/api/v1/saves/", _target(world), format="json")
    client_for(other).post(
        "/api/v1/saves/",
        {"content_type": "post", "object_id": other_post.pk},
        format="json",
    )

    mine = client_for(me).get("/api/v1/saves/me/").json()["results"]
    theirs = client_for(other).get("/api/v1/saves/me/").json()["results"]

    assert [item["object_id"] for item in mine] == [world.post.pk]
    assert [item["object_id"] for item in theirs] == [other_post.pk]


def test_spoofed_user_fields_in_the_body_are_ignored(world):
    attacker, victim = make_user(), make_user()
    client = client_for(attacker)
    target = {**_target(world), "user": victim.pk, "follower": victim.pk}

    follow = client.post(
        f"/api/v1/businesses/{world.business.pk}/follow/",
        {"follower": victim.pk},
        format="json",
    )
    like = client.post("/api/v1/likes/", target, format="json")
    save = client.post("/api/v1/saves/", target, format="json")
    comment = client.post(
        "/api/v1/comments/", {**target, "text": "spoof"}, format="json"
    )
    share = client.post("/api/v1/shares/", target, format="json")

    assert follow.status_code == 200
    assert like.status_code == 200
    assert save.status_code == 200
    assert comment.status_code == 201
    assert share.status_code == 201
    assert Follow.objects.get().follower_id == attacker.pk
    assert Like.objects.get().user_id == attacker.pk
    assert Save.objects.get().user_id == attacker.pk
    assert Comment.objects.get().user_id == attacker.pk
    assert Share.objects.get().user_id == attacker.pk


@pytest.mark.parametrize(
    "label",
    ["follow", "unfollow", "like", "unlike", "save", "unsave", "comment", "share"],
)
def test_unknown_target_is_404_with_envelope_and_changes_nothing(world, label):
    rows_before = _row_counts()
    method, url, payload = _requests(world)[label]
    url = url.replace(f"/{world.business.pk}/", "/999999/")
    if payload:
        payload = {**payload, "object_id": 999999}

    response = _send(client_for(make_user()), method, url, payload)

    assert_not_found(response)
    assert _row_counts() == rows_before


def test_hidden_comment_is_visible_only_to_author_and_moderators(world):
    author, bystander = make_user(), make_user()
    staff_without_capability = make_user()
    staff_without_capability.is_staff = True
    staff_without_capability.save()
    hidden = Comment.objects.create(
        user=author,
        content_type=ContentType.objects.get_for_model(Post),
        object_id=world.post.pk,
        text="auto-hidden comment",
        is_hidden=True,
    )

    def listed_ids(viewer):
        response = client_for(viewer).get("/api/v1/comments/", _target(world))
        assert response.status_code == 200
        return [item["id"] for item in response.json()["results"]]

    assert hidden.id in listed_ids(author)  # positive control
    for viewer in (None, bystander, world.owner, staff_without_capability):
        assert hidden.id not in listed_ids(viewer)


def test_finding_s1_like_on_unpublished_post_is_currently_accepted(world):
    """Characterisation of open finding S-1 - see module docstring."""
    pending = make_post(world.business, published=False, caption="pending")

    response = client_for(make_user()).post(
        "/api/v1/likes/",
        {"content_type": "post", "object_id": pending.pk},
        format="json",
    )

    assert response.status_code == 200
    pending.refresh_from_db()
    assert pending.likes_count == 1
'@
Write-RepoFile "social\tests\test_permission_sweep.py" $socialSweep

# ---------------------------------------------------------------------
# 3) reports/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$reportsSweep = @'
"""
P-096 (step 2) permission sweep for the reports app.

The only route is POST /api/v1/reports/ (create-only; no read, edit or
delete route exists, so a reporter's identity and a report's status can
never be read or changed through the API). The reports/tests/conftest
`business` fixture and its autouse throttle-cache reset apply here.

Category 1: no token / garbage token -> 401 envelope, no Report row.
Category 2: the reporter is always request.user (a spoofed `reporter`
            or `user` in the body is ignored); no GET/PUT/PATCH/DELETE
            handler exists, and no detail route exists, so an existing
            report cannot be altered.
Category 3: reports have no capability-gated API route (triage is in
            Django Admin, covered by reports/tests/test_admin.py).
"""

import pytest

from core.tests.sweep_factories import (
    client_for,
    garbage_token_client,
    make_post,
    make_user,
)
from core.tests.sweep_helpers import assert_error_envelope, assert_unauthenticated
from reports.models import Report

pytestmark = pytest.mark.django_db

URL = "/api/v1/reports/"


def _payload(post, **extra):
    return {"content_type": "post", "object_id": post.pk, "reason": "spam", **extra}


@pytest.mark.parametrize("client_factory", [client_for, garbage_token_client])
def test_unauthenticated_gets_401_and_creates_no_report(business, client_factory):
    post = make_post(business)

    response = client_factory().post(URL, _payload(post), format="json")

    assert_unauthenticated(response)
    assert Report.objects.count() == 0


def test_spoofed_reporter_in_the_body_is_ignored(business):
    post = make_post(business)
    reporter, victim = make_user(), make_user()

    response = client_for(reporter).post(
        URL, _payload(post, reporter=victim.pk, user=victim.pk), format="json"
    )

    assert response.status_code == 201
    assert Report.objects.get().reporter_id == reporter.pk


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_no_read_or_modify_handler_exists_on_the_collection(business, method):
    post = make_post(business)
    reporter = make_user()
    client = client_for(reporter)
    client.post(URL, _payload(post), format="json")
    report = Report.objects.get()

    response = getattr(client, method)(URL)

    assert_error_envelope(response, 405, "METHOD_NOT_ALLOWED")
    assert Report.objects.count() == 1
    report.refresh_from_db()
    assert report.status == Report.Status.OPEN


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_no_detail_route_exists_for_a_report(business, method):
    post = make_post(business)
    client = client_for(make_user())
    client.post(URL, _payload(post), format="json")
    report = Report.objects.get()

    response = getattr(client, method)(f"{URL}{report.pk}/")

    assert response.status_code == 404
    assert Report.objects.filter(pk=report.pk).exists()
'@
Write-RepoFile "reports\tests\test_permission_sweep.py" $reportsSweep

# ---------------------------------------------------------------------
# 4) ratings/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$ratingsSweep = @'
"""
P-096 (step 2) permission sweep for the ratings app.

Routes: POST /businesses/{id}/rate/ (authenticated upsert) and the
public GET /businesses/{id}/ratings/.

Category 1: no token / garbage token -> 401 envelope, no Rating row,
            the business aggregate untouched.
Category 2: the rating's author is always request.user (spoofed
            `customer`, `average_rating`, `ratings_count` in the body
            are ignored); user B rating never edits user A's row;
            unknown business -> 404 envelope.
Category 3: no capability-gated route exists in this app.

OPEN FINDING S-2 (characterised, NOT changed here): nothing forbids a
business owner from rating their OWN business, which lets an owner
inflate average_rating (used by Search's min_rating filter). The
architecture does not state a rule either way, so this needs a product
decision. The last test pins today's behaviour.
"""

from decimal import Decimal

import pytest

from core.tests.sweep_factories import (
    client_for,
    garbage_token_client,
    make_business,
    make_user,
)
from core.tests.sweep_helpers import assert_not_found, assert_unauthenticated
from ratings.models import Rating

pytestmark = pytest.mark.django_db


def _rate_url(pk):
    return f"/api/v1/businesses/{pk}/rate/"


@pytest.mark.parametrize("client_factory", [client_for, garbage_token_client])
def test_unauthenticated_gets_401_and_changes_nothing(client_factory):
    business = make_business()

    response = client_factory().post(_rate_url(business.pk), {"score": 5})

    assert_unauthenticated(response)
    assert Rating.objects.count() == 0
    business.refresh_from_db()
    assert business.ratings_count == 0
    assert business.average_rating == 0


def test_unknown_business_is_404_with_envelope_and_creates_nothing():
    response = client_for(make_user()).post(_rate_url(999999), {"score": 5})

    assert_not_found(response)
    assert Rating.objects.count() == 0


def test_spoofed_customer_and_aggregates_in_the_body_are_ignored():
    business = make_business()
    attacker, victim = make_user(), make_user()

    response = client_for(attacker).post(
        _rate_url(business.pk),
        {
            "score": 5,
            "customer": victim.pk,
            "average_rating": 1,
            "ratings_count": 99,
        },
        format="json",
    )

    assert response.status_code == 200
    rating = Rating.objects.get()
    assert rating.customer_id == attacker.pk
    business.refresh_from_db()
    assert business.ratings_count == 1
    assert business.average_rating == Decimal("5.00")


def test_another_users_rating_never_edits_my_row():
    business = make_business()
    me, other = make_user(), make_user()
    client_for(me).post(_rate_url(business.pk), {"score": 2}, format="json")

    client_for(other).post(
        _rate_url(business.pk),
        {"score": 5, "review_text": "other review"},
        format="json",
    )

    mine = Rating.objects.get(customer=me)
    assert mine.score == 2
    assert mine.review_text == ""
    assert Rating.objects.count() == 2
    business.refresh_from_db()
    assert business.ratings_count == 2
    assert business.average_rating == Decimal("3.50")


def test_public_ratings_list_stays_open_to_anonymous_users():
    business = make_business()

    response = client_for().get(f"/api/v1/businesses/{business.pk}/ratings/")

    assert response.status_code == 200


def test_finding_s2_owner_can_currently_rate_their_own_business():
    """Characterisation of open finding S-2 - see module docstring."""
    business = make_business()

    response = client_for(business.user).post(
        _rate_url(business.pk), {"score": 5}, format="json"
    )

    assert response.status_code == 200
    assert Rating.objects.get().customer_id == business.user_id
'@
Write-RepoFile "ratings\tests\test_permission_sweep.py" $ratingsSweep

# ---------------------------------------------------------------------
# 5) feed/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$feedSweep = @'
"""
P-096 (step 2) permission sweep for the feed app (home + discover).

Both routes are authenticated GETs that derive everything from
request.user; there is no id parameter to attack.

Category 1: no token / garbage token -> 401 envelope on both routes.
Category 2: the feed (including the 90 s per-user cached first page)
            is never shared between users, and `user`/`user_id` query
            parameters are ignored.
Category 3: no capability-gated route exists in this app.
"""

import pytest
from django.core.cache import cache
from django.urls import reverse

from core.tests.sweep_factories import client_for, garbage_token_client
from core.tests.sweep_helpers import assert_unauthenticated
from feed.tests.helpers import make_business, make_customer, make_follow, make_post, ts

pytestmark = pytest.mark.django_db

ROUTES = ["home-feed", "discover-feed"]


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def _first_post_id(response):
    assert response.status_code == 200
    return response.json()["items"][0]["id"]


@pytest.mark.parametrize("client_factory", [client_for, garbage_token_client])
@pytest.mark.parametrize("route", ROUTES)
def test_unauthenticated_gets_401_envelope(route, client_factory):
    response = client_factory().get(reverse(route))

    assert_unauthenticated(response)


def test_home_feed_is_never_shared_between_users_even_when_cached():
    alice, bob = make_customer(), make_customer()
    biz_alice, biz_bob = make_business("Alice Biz"), make_business("Bob Biz")
    make_follow(alice, biz_alice)
    make_follow(bob, biz_bob)
    alice_post = make_post(biz_alice, at=ts(1))
    bob_post = make_post(biz_bob, at=ts(2))
    url = reverse("home-feed")

    first_alice = _first_post_id(client_for(alice).get(url))  # fills her cache
    first_bob = _first_post_id(client_for(bob).get(url))
    second_alice = _first_post_id(client_for(alice).get(url))  # served from cache

    assert first_alice == second_alice == alice_post.id
    assert first_bob == bob_post.id


def test_user_query_parameters_cannot_select_another_users_feed():
    alice, bob = make_customer(), make_customer()
    biz_alice, biz_bob = make_business("Alice Biz"), make_business("Bob Biz")
    make_follow(alice, biz_alice)
    make_follow(bob, biz_bob)
    alice_post = make_post(biz_alice, at=ts(1))
    make_post(biz_bob, at=ts(2))

    response = client_for(alice).get(
        reverse("home-feed"), {"user": bob.pk, "user_id": bob.pk}
    )

    assert _first_post_id(response) == alice_post.id


def test_discover_feed_never_includes_unpublished_content():
    viewer = make_customer()
    biz = make_business("Discover Biz")
    published = make_post(biz, at=ts(1))
    make_post(biz, at=ts(2), status="pending_review", caption="pending")
    make_post(biz, at=ts(3), status="rejected", caption="rejected")

    response = client_for(viewer).get(reverse("discover-feed"))

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [published.id]
'@
Write-RepoFile "feed\tests\test_permission_sweep.py" $feedSweep

# ---------------------------------------------------------------------
# 6) search/tests/test_permission_sweep.py
# ---------------------------------------------------------------------
$searchSweep = @'
"""
P-096 (step 2) permission sweep for the search app.

Search is public by design (AllowAny), so "401" does not apply; the
sweep instead proves the public surface leaks nothing it should not:

Category 1 (adapted): anonymous access is allowed and returns exactly
            what a logged-in user gets (no personalised data).
Category 2 (adapted): inactive and soft-deleted products never appear
            in results (with a positive control: the active product
            with the same keyword IS found), and hostile query strings
            never cause a 500.
Category 3: no capability-gated route exists in this app.
"""

import pytest
from django.urls import reverse

from core.tests.sweep_factories import client_for
from products.models import Product
from search.tests.test_api import (
    _names,
    make_business,
    make_category,
    make_customer,
    make_product,
)

pytestmark = pytest.mark.django_db

SEARCH_URL = reverse("search")


def test_anonymous_and_authenticated_users_get_identical_results():
    category = make_category()
    biz = make_business("Sweepium Traders")
    make_product(biz, category, "Sweepium Lamp")

    anonymous = client_for().get(SEARCH_URL, {"q": "sweepium"})
    logged_in = client_for(make_customer()).get(SEARCH_URL, {"q": "sweepium"})

    assert anonymous.status_code == logged_in.status_code == 200
    assert anonymous.json() == logged_in.json()
    assert "Sweepium Lamp" in _names(anonymous.json())


def test_inactive_and_soft_deleted_products_never_appear():
    category = make_category()
    biz = make_business("Keyword House")
    make_product(biz, category, "Keyworditem Active")
    inactive = make_product(biz, category, "Keyworditem Inactive")
    Product.objects.filter(pk=inactive.pk).update(is_active=False)
    deleted = make_product(biz, category, "Keyworditem Deleted")
    deleted.delete()  # soft delete

    response = client_for().get(SEARCH_URL, {"q": "keyworditem"})

    assert response.status_code == 200
    names = _names(response.json())
    assert "Keyworditem Active" in names  # positive control
    assert "Keyworditem Inactive" not in names
    assert "Keyworditem Deleted" not in names


@pytest.mark.parametrize(
    "hostile_q",
    [
        "'; DROP TABLE products_product; --",
        "<script>alert(1)</script>",
        "a" * 500,
    ],
    ids=["sql-injection", "script-tag", "very-long"],
)
def test_hostile_query_strings_never_cause_a_server_error(hostile_q):
    category = make_category()
    make_product(make_business("Safe Biz"), category, "Safe Product")

    response = client_for().get(SEARCH_URL, {"q": hostile_q})

    assert response.status_code == 200
    assert Product.objects.count() == 1
'@
Write-RepoFile "search\tests\test_permission_sweep.py" $searchSweep

Write-Host ""
Write-Host "Done: 6 files written. No existing file was modified." -ForegroundColor Cyan
Write-Host "Next: format + run the checks from the STEP 2 message."