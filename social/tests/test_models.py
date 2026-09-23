import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from businesses.models import BusinessProfile
from social.models import Follow

User = get_user_model()


def _make_follower():
    return User.objects.create_user(
        username="follower1", password="pass12345", account_type="customer"
    )


def _make_business():
    owner = User.objects.create_user(
        username="owner1", password="pass12345", account_type="business"
    )
    return BusinessProfile.objects.create(
        user=owner,
        business_name="Test Business",
        business_type="trader",
        country="Egypt",
        city="Cairo",
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
