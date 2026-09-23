import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from businesses.models import BusinessProfile
from categories.models import Category
from content.models import Post, Reel
from products.models import Product
from social.models import Follow, Like, Save
from uuid import uuid4

User = get_user_model()

_VALID_MP4_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


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


def _make_category(name="Fashion"):
    return Category.objects.create(name=name)


def _make_product(business=None, category=None, **overrides):
    defaults = {
        "business": business or _make_business(),
        "category": category or _make_category(),
        "name": "Classic Shirt",
        "description": "A shirt.",
        "price": "199.99",
        "currency": Product.CURRENCY_EGP,
    }
    defaults.update(overrides)
    return Product.objects.create(**defaults)


def _make_saver(suffix="saver1"):
    return User.objects.create_user(
        username=suffix, password="pass12345", account_type="customer"
    )


@pytest.mark.django_db
class TestSaveModel:
    def test_save_created_successfully_on_post(self):
        saver = _make_saver("saver-p1")
        post = _make_post()
        content_type = ContentType.objects.get_for_model(Post)

        save = Save.objects.create(
            user=saver, content_type=content_type, object_id=post.pk
        )

        assert save.pk is not None
        assert save.content_object == post
        assert saver.saves.count() == 1

    def test_save_created_successfully_on_reel(self):
        saver = _make_saver("saver-r1")
        reel = _make_reel()
        content_type = ContentType.objects.get_for_model(Reel)

        save = Save.objects.create(
            user=saver, content_type=content_type, object_id=reel.pk
        )

        assert save.pk is not None
        assert save.content_object == reel

    def test_save_created_successfully_on_product(self):
        """
        Proves Save's generic-FK dispatch works against Product too —
        the one content type Like deliberately does NOT support
        (P-053 stays Post/Reel-only; P-054 adds Product per its own
        spec interpretation).
        """
        saver = _make_saver("saver-pr1")
        product = _make_product()
        content_type = ContentType.objects.get_for_model(Product)

        save = Save.objects.create(
            user=saver, content_type=content_type, object_id=product.pk
        )

        assert save.pk is not None
        assert save.content_object == product

    def test_duplicate_save_raises_integrity_error(self):
        saver = _make_saver("saver-dup")
        post = _make_post()
        content_type = ContentType.objects.get_for_model(Post)
        Save.objects.create(user=saver, content_type=content_type, object_id=post.pk)

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Save.objects.create(
                    user=saver, content_type=content_type, object_id=post.pk
                )

    def test_same_user_can_save_post_reel_and_product_independently(self):
        saver = _make_saver("saver-all3")
        post = _make_post()
        reel = _make_reel()
        product = _make_product()

        Save.objects.create(
            user=saver,
            content_type=ContentType.objects.get_for_model(Post),
            object_id=post.pk,
        )
        Save.objects.create(
            user=saver,
            content_type=ContentType.objects.get_for_model(Reel),
            object_id=reel.pk,
        )
        Save.objects.create(
            user=saver,
            content_type=ContentType.objects.get_for_model(Product),
            object_id=product.pk,
        )

        assert Save.objects.filter(user=saver).count() == 3

    def test_saver_deleted_cascades(self):
        saver = _make_saver("saver-cascade")
        post = _make_post()
        content_type = ContentType.objects.get_for_model(Post)
        Save.objects.create(user=saver, content_type=content_type, object_id=post.pk)

        saver.delete()

        assert Save.objects.count() == 0
