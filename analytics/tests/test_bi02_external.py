"""BI-02 external (other platform) metrics: owner-entered snapshots."""

import pytest

from analytics import external
from analytics.models import ExternalContentMetricSnapshot
from analytics.tests.bi02_helpers import (
    RANGE,
    DAY1,
    build_scenario,
    make_content_daily,
    make_business,
    make_post,
    url,
)
from core.tests.sweep_factories import client_for

pytestmark = pytest.mark.django_db

T1 = "2026-03-01T00:00:00Z"
T2 = "2026-03-02T00:00:00Z"


@pytest.fixture(autouse=True)
def _empty_registry(monkeypatch):
    monkeypatch.setattr(external, "_REGISTRY", {})


@pytest.fixture
def s():
    return build_scenario()


def ext(s, kind="post", obj=None):
    obj = obj or (s.post if kind == "post" else s.reel)
    return url(s.business, f"content/{kind}/{obj.pk}/external-metrics/")


def owner(s):
    return client_for(s.business.user)


def add(s, **overrides):
    data = {
        "platform": "instagram",
        "metric_type": "views",
        "metric_value": 500,
        "captured_at": T1,
    }
    data.update(overrides)
    return owner(s).post(ext(s), data, format="json")


def test_manual_entry_is_stored_with_source_manual(s):
    r = add(s, external_reference="https://instagram.com/p/abc")
    assert r.status_code == 201
    assert r.json() == {
        "platform": "instagram",
        "metric_type": "views",
        "value": 500,
        "source": "manual",
        "last_updated": T1,
        "external_reference": "https://instagram.com/p/abc",
    }
    snap = ExternalContentMetricSnapshot.objects.get()
    assert (snap.business_id, snap.content_type, snap.content_id) == (
        s.business.pk,
        "post",
        s.post.pk,
    )


def test_client_cannot_choose_source_or_business(s):
    other = make_business()
    r = add(s, source="synced", business=other.pk, business_id=other.pk)
    assert r.status_code == 201
    snap = ExternalContentMetricSnapshot.objects.get()
    assert snap.source == "manual"
    assert snap.business_id == s.business.pk


def test_snapshots_accumulate_and_latest_wins(s):
    add(s, metric_value=500, captured_at=T1)
    add(s, metric_value=700, captured_at=T2)
    add(s, platform="tiktok", metric_value=50, captured_at=T1)
    assert ExternalContentMetricSnapshot.objects.count() == 3  # nothing updated

    body = owner(s).get(ext(s)).json()
    assert body["content_type"] == "post" and body["content_id"] == s.post.pk
    assert [(m["platform"], m["metric_type"], m["value"]) for m in body["metrics"]] == [
        ("instagram", "views", 700),
        ("tiktok", "views", 50),
    ]
    assert body["metrics"][0]["last_updated"] == T2
    assert all(m["source"] == "manual" for m in body["metrics"])
    assert body["providers_registered"] == []


def test_same_captured_at_later_entry_wins(s):
    add(s, metric_value=1, captured_at=T1)
    add(s, metric_value=2, captured_at=T1)
    metrics = owner(s).get(ext(s)).json()["metrics"]
    assert [m["value"] for m in metrics] == [2]


def test_reels_work_too(s):
    r = owner(s).post(
        ext(s, "reel"),
        {"platform": "facebook", "metric_type": "likes", "metric_value": 9},
        format="json",
    )
    assert r.status_code == 201
    assert r.json()["last_updated"].endswith("Z")
    assert ExternalContentMetricSnapshot.objects.get().content_type == "reel"


@pytest.mark.parametrize(
    "overrides",
    [
        {"platform": "myspace"},
        {"metric_type": "reach"},
        {"metric_value": -1},
        {"metric_value": "abc"},
        {"captured_at": "2999-01-01T00:00:00Z"},
    ],
)
def test_invalid_manual_entries_are_400(s, overrides):
    r = add(s, **overrides)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert ExternalContentMetricSnapshot.objects.count() == 0


def test_missing_value_is_400(s):
    r = owner(s).post(
        ext(s), {"platform": "instagram", "metric_type": "views"}, format="json"
    )
    assert r.status_code == 400


def test_external_numbers_are_never_merged_with_internal(s):
    make_content_daily("post", s.post, DAY1, views=10, unique_views=5)
    add(s, metric_value=12345)
    body = owner(s).get(url(s.business, f"content/post/{s.post.pk}/"), RANGE).json()
    assert body["internal"]["totals"]["views"] == 10
    assert [m["value"] for m in body["external"]["metrics"]] == [12345]
    assert "Never merged" in body["external"]["note"]


def test_non_owner_cannot_read_or_write(s):
    attacker = make_business()
    client = client_for(attacker.user)
    assert client.get(ext(s)).status_code == 403
    r = client.post(
        ext(s),
        {"platform": "instagram", "metric_type": "views", "metric_value": 1},
        format="json",
    )
    assert r.status_code == 403
    assert ExternalContentMetricSnapshot.objects.count() == 0


def test_cannot_attach_to_another_businesses_content(s):
    attacker = make_business()
    victim_post = make_post(s.business)
    own_url = url(attacker, f"content/post/{victim_post.pk}/external-metrics/")
    client = client_for(attacker.user)
    assert client.get(own_url).status_code == 404
    r = client.post(
        own_url,
        {"platform": "instagram", "metric_type": "views", "metric_value": 1},
        format="json",
    )
    assert r.status_code == 404
    assert ExternalContentMetricSnapshot.objects.count() == 0
