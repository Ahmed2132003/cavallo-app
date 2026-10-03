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
