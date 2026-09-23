import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from businesses.models import BusinessProfile
from content.models import Post, Reel
from social.models import Follow, Like
from uuid import uuid4

User = get_user_model()

_VALID_MP4_BYTES = (
    b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
)


def _make_follower():
    return User.objects.create_user(
        username="follower1", password="pass12345", account_type="customer"
    )


def _make_business():
    owner = User.objects.create_user(
        username=f"owner-{uuid4().hex[:12]}",
        password="pass12345",
        account_type="business",
    )
    return BusinessProfile.objects.create(
        user=owner,
        business_name="Test Business",
        business_type="trader",
        country="Egypt",
        city="Cairo",
    )


def _make_liker(suffix="liker1"):
    return User.objects.create_user(
        username=suffix, password="pass12345", account_type="customer"
    )


def _make_post(business=None):
    business = business or _make_business()
    return Post.objects.create(business=business, caption="hi")


def _make_reel(business=None):
    business = business or _make_business()
    return Reel.objects.create(
        business=business,
        caption="hi",
        video=SimpleUploadedFile("raw.mp4", _VALID_MP4_BYTES, content_type="video/mp4"),
    )


@pytest.mark.django_db
class TestFollowModel:
    def test_follow_created_successfully(self):
        follower = _make_follower()
        business = _make_business()
        follow = Follow.objects.create(follower=follower, business=business)
        assert follow.pk is not None
        assert business.followers.count() == 1
        assert follower.following.count() == 1

    def test_duplicate_follow_raises_integrity_error(self):
        follower = _make_follower()
        business = _make_business()
        Follow.objects.create(follower=follower, business=business)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Follow.objects.create(follower=follower, business=business)

    def test_follower_deleted_cascades(self):
        follower = _make_follower()
        business = _make_business()
        Follow.objects.create(follower=follower, business=business)
        follower.delete()
        assert Follow.objects.count() == 0

    def test_business_soft_delete_does_not_cascade(self):
        """
        SoftDeleteModel.delete() doesn't remove the row, so the
        FK's on_delete=CASCADE never fires — the Follow row must
        survive a soft-deleted business. Documents this on purpose
        (not a bug) so a future part doesn't "fix" it by mistake.
        """
        follower = _make_follower()
        business = _make_business()
        Follow.objects.create(follower=follower, business=business)
        business.delete()
        assert Follow.objects.count() == 1

    def test_business_hard_delete_cascades(self):
        follower = _make_follower()
        business = _make_business()
        Follow.objects.create(follower=follower, business=business)
        business.hard_delete()
        assert Follow.objects.count() == 0


@pytest.mark.django_db
class TestLikeModel:
    def test_like_created_successfully_on_post(self):
        liker = _make_liker("liker-p1")
        post = _make_post()
        content_type = ContentType.objects.get_for_model(Post)

        like = Like.objects.create(
            user=liker, content_type=content_type, object_id=post.pk
        )

        assert like.pk is not None
        assert like.content_object == post
        assert liker.likes.count() == 1

    def test_like_created_successfully_on_reel(self):
        liker = _make_liker("liker-r1")
        reel = _make_reel()
        content_type = ContentType.objects.get_for_model(Reel)

        like = Like.objects.create(
            user=liker, content_type=content_type, object_id=reel.pk
        )

        assert like.pk is not None
        assert like.content_object == reel

    def test_duplicate_like_raises_integrity_error(self):
        liker = _make_liker("liker-dup")
        post = _make_post()
        content_type = ContentType.objects.get_for_model(Post)
        Like.objects.create(user=liker, content_type=content_type, object_id=post.pk)

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Like.objects.create(
                    user=liker, content_type=content_type, object_id=post.pk
                )

    def test_same_user_can_like_a_post_and_a_reel_independently(self):
        """
        unique_together is (user, content_type, object_id) — liking a
        Post and a Reel that happen to share the same numeric pk must
        NOT collide, because content_type differs. Proves the generic
        FK's uniqueness scoping actually uses content_type, not just
        object_id.
        """
        liker = _make_liker("liker-both")
        post = _make_post()
        reel = _make_reel()
        post_ct = ContentType.objects.get_for_model(Post)
        reel_ct = ContentType.objects.get_for_model(Reel)

        Like.objects.create(user=liker, content_type=post_ct, object_id=post.pk)
        Like.objects.create(user=liker, content_type=reel_ct, object_id=reel.pk)

        assert Like.objects.filter(user=liker).count() == 2

    def test_liker_deleted_cascades(self):
        liker = _make_liker("liker-cascade")
        post = _make_post()
        content_type = ContentType.objects.get_for_model(Post)
        Like.objects.create(user=liker, content_type=content_type, object_id=post.pk)

        liker.delete()

        assert Like.objects.count() == 0