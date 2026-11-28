"""BI-02: POST /api/v1/analytics/events/views/."""

import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.core.cache import cache

from analytics import constants as C
from analytics.models import ContentViewEvent, ProductDailyStats, ProductViewEvent
from analytics.tests.bi02_helpers import (
    make_business,
    make_customer,
    make_post,
    make_product,
    make_reel,
)
from analytics.tracking import build_viewer_key
from content.models import Post, Reel
from core.tests.sweep_factories import client_for

pytestmark = pytest.mark.django_db

EVENT_URL = "/api/v1/analytics/events/views/"
ANON = "anon-install-0123456789abcdef"


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


def payload(target_type, target_id, **extra):
    data = {
        "event_id": str(uuid.uuid4()),
        "target_type": target_type,
        "target_id": target_id,
    }
    data.update(extra)
    return data


def post_event(client, data):
    return client.post(EVENT_URL, data, format="json")


# ------------------------------------------------------------ anonymous
def test_anonymous_product_view_is_recorded_without_ip():
    product = make_product(make_business())
    r = post_event(client_for(), payload("product", product.pk, anon_id=ANON))
    assert r.status_code == 201
    assert r.json()["status"] == "recorded"
    event = ProductViewEvent.objects.get()
    assert event.product_id == product.pk
    assert event.business_id == product.business_id
    assert event.viewer_key == build_viewer_key(user=None, anon_id=ANON)
    assert len(event.viewer_key) == 64
    assert ANON not in event.viewer_key
    names = {f.name for f in ProductViewEvent._meta.get_fields()}
    assert names == {
        "id",
        "event_id",
        "viewer_key",
        "product",
        "business",
        "occurred_at",
        "view_date",
        "created_at",
    }


def test_anonymous_without_anon_id_is_rejected():
    product = make_product(make_business())
    r = post_event(client_for(), payload("product", product.pk))
    assert r.status_code == 400
    assert "anon_id" in r.json()["error"]["fields"]
    assert ProductViewEvent.objects.count() == 0


def test_malformed_anon_id_is_rejected():
    product = make_product(make_business())
    r = post_event(client_for(), payload("product", product.pk, anon_id="short"))
    assert r.status_code == 400
    assert ProductViewEvent.objects.count() == 0


# ------------------------------------------------------------ logged in
def test_logged_in_viewer_key_comes_from_token_not_body():
    product = make_product(make_business())
    c1, c2 = make_customer(), make_customer()
    r1 = post_event(client_for(c1), payload("product", product.pk, anon_id=ANON))
    r2 = post_event(client_for(c2), payload("product", product.pk))
    assert r1.status_code == 201 and r2.status_code == 201
    keys = set(ProductViewEvent.objects.values_list("viewer_key", flat=True))
    assert keys == {
        build_viewer_key(user=c1, anon_id=None),
        build_viewer_key(user=c2, anon_id=None),
    }


# ------------------------------------------------------------ duplicates
def test_duplicate_event_id_is_a_noop():
    product = make_product(make_business())
    data = payload("product", product.pk, anon_id=ANON)
    first = post_event(client_for(), data)
    second = post_event(client_for(), data)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert ProductViewEvent.objects.count() == 1


def test_duplicate_event_id_from_another_viewer_is_still_a_noop():
    product = make_product(make_business())
    data = payload("product", product.pk, anon_id=ANON)
    post_event(client_for(), data)
    r = post_event(client_for(make_customer()), data)
    assert r.status_code == 200
    assert ProductViewEvent.objects.count() == 1


def test_event_id_must_be_a_uuid():
    product = make_product(make_business())
    data = payload("product", product.pk, anon_id=ANON)
    data["event_id"] = "abc"
    assert post_event(client_for(), data).status_code == 400


# ------------------------------------------------------------ owner / target
def test_owner_viewing_own_items_is_rejected():
    business = make_business()
    product = make_product(business)
    post = make_post(business)
    client = client_for(business.user)
    assert post_event(client, payload("product", product.pk)).status_code == 403
    assert post_event(client, payload("post", post.pk)).status_code == 403
    assert ProductViewEvent.objects.count() == 0
    assert ContentViewEvent.objects.count() == 0


def test_missing_or_inactive_targets_are_404():
    business = make_business()
    product = make_product(business)
    client = client_for(make_customer())

    assert post_event(client, payload("product", 999999)).status_code == 404

    type(product)._base_manager.filter(pk=product.pk).update(is_active=False)
    assert post_event(client, payload("product", product.pk)).status_code == 404

    gone = make_product(business)
    gone.delete()
    assert post_event(client, payload("product", gone.pk)).status_code == 404

    pending = Post.objects.create(business=business, caption="pending")
    assert post_event(client, payload("post", pending.pk)).status_code == 404

    unready = Reel.objects.create(
        business=business, caption="x", video="reels/videos/x.mp4"
    )
    Reel._base_manager.filter(pk=unready.pk).update(status="published")
    assert (
        post_event(client, payload("reel", unready.pk, watch_ms=9000)).status_code
        == 404
    )

    assert ProductViewEvent.objects.count() == 0
    assert ContentViewEvent.objects.count() == 0


def test_soft_deleted_business_target_is_404():
    business = make_business()
    post = make_post(business)
    business.delete()
    r = post_event(client_for(make_customer()), payload("post", post.pk))
    assert r.status_code == 404


# ------------------------------------------------------------ content / reel
def test_post_view_is_recorded_with_no_watch_time():
    post = make_post(make_business())
    r = post_event(client_for(make_customer()), payload("post", post.pk))
    assert r.status_code == 201
    event = ContentViewEvent.objects.get()
    assert (event.content_kind, event.object_id, event.watch_ms) == (
        "post",
        post.pk,
        None,
    )


def test_watch_ms_only_accepted_for_reels():
    business = make_business()
    client = client_for(make_customer())
    for kind, obj in (
        ("post", make_post(business)),
        ("product", make_product(business)),
    ):
        r = post_event(client, payload(kind, obj.pk, watch_ms=5000))
        assert r.status_code == 400
        assert "watch_ms" in r.json()["error"]["fields"]


def test_reel_below_minimum_watch_time_is_ignored(settings):
    settings.ANALYTICS_REEL_MIN_WATCH_MS = 3000
    reel = make_reel(make_business())
    client = client_for(make_customer())

    for extra in ({}, {"watch_ms": 0}, {"watch_ms": 2999}):
        r = post_event(client, payload("reel", reel.pk, **extra))
        assert r.status_code == 200
        assert r.json() == {
            "status": "ignored",
            "reason": "below_min_watch_ms",
            "min_watch_ms": 3000,
        }
    assert ContentViewEvent.objects.count() == 0

    ok = post_event(client, payload("reel", reel.pk, watch_ms=3000))
    assert ok.status_code == 201
    assert ContentViewEvent.objects.get().watch_ms == 3000


def test_reel_watch_ms_above_hard_cap_is_rejected():
    reel = make_reel(make_business())
    r = post_event(
        client_for(make_customer()),
        payload("reel", reel.pk, watch_ms=C.MAX_WATCH_MS + 1),
    )
    assert r.status_code == 400


# ------------------------------------------------------------ occurred_at
def test_occurred_at_window_and_view_date():
    product = make_product(make_business())
    client = client_for(make_customer())
    now = datetime.now(dt_timezone.utc)

    future = now + timedelta(minutes=30)
    old = now - timedelta(hours=C.EVENT_MAX_AGE_HOURS + 1)
    assert (
        post_event(
            client, payload("product", product.pk, occurred_at=future.isoformat())
        ).status_code
        == 400
    )
    assert (
        post_event(
            client, payload("product", product.pk, occurred_at=old.isoformat())
        ).status_code
        == 400
    )

    slightly_ahead = now + timedelta(minutes=2)
    assert (
        post_event(
            client,
            payload("product", product.pk, occurred_at=slightly_ahead.isoformat()),
        ).status_code
        == 201
    )

    earlier = now - timedelta(hours=2)
    r = post_event(
        client, payload("product", product.pk, occurred_at=earlier.isoformat())
    )
    assert r.status_code == 201
    assert ProductViewEvent.objects.get(event_id=r.json()["event_id"]).view_date == (
        earlier.date()
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_type": "story"},
        {"target_type": "nonsense"},
        {"target_id": 0},
        {"target_id": "abc"},
    ],
)
def test_invalid_targets_are_400(overrides):
    product = make_product(make_business())
    data = payload("product", product.pk, anon_id=ANON)
    data.update(overrides)
    assert post_event(client_for(), data).status_code == 400


# ------------------------------------------------------------ no counters
@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_only_post_is_allowed(method):
    for client in (client_for(), client_for(make_customer())):
        assert getattr(client, method)(EVENT_URL).status_code == 405


def test_no_request_body_can_set_a_counter():
    product = make_product(make_business())
    data = payload(
        "product",
        product.pk,
        anon_id=ANON,
        views=999,
        unique_views=999,
        views_count=999,
        likes=999,
    )
    r = post_event(client_for(), data)
    assert r.status_code == 201
    assert set(r.json()) == {"status", "event_id"}
    assert ProductViewEvent.objects.count() == 1
    assert ProductDailyStats.objects.count() == 0


# ------------------------------------------------------------ throttling
def test_anonymous_throttle_returns_429(settings):
    settings.ANALYTICS_VIEW_EVENT_ANON_RATE = "3/min"
    product = make_product(make_business())
    client = client_for()
    codes = [
        post_event(client, payload("product", product.pk, anon_id=ANON)).status_code
        for _ in range(4)
    ]
    assert codes == [201, 201, 201, 429]
    last = post_event(client, payload("product", product.pk, anon_id=ANON))
    assert last.json()["error"]["code"] == "THROTTLED"


def test_user_throttle_is_separate_from_anonymous_rate(settings):
    settings.ANALYTICS_VIEW_EVENT_ANON_RATE = "1/min"
    settings.ANALYTICS_VIEW_EVENT_USER_RATE = "3/min"
    product = make_product(make_business())
    client = client_for(make_customer())
    codes = [
        post_event(client, payload("product", product.pk)).status_code for _ in range(4)
    ]
    assert codes == [201, 201, 201, 429]
