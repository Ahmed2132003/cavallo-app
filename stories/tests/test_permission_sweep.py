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
    StoryView.objects.create(
        story=world["story"], viewer=_make_customer("v@example.com")
    )
    api_client.force_authenticate(user)
    views_before = StoryView.objects.count()

    response = api_client.get(f"/api/v1/stories/{world['story'].id}/view-count/")

    assert_forbidden(response)
    assert "view_count" not in response.json()
    assert StoryView.objects.count() == views_before


def test_unknown_story_is_404_with_envelope_for_view_and_view_count(api_client, world):
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
