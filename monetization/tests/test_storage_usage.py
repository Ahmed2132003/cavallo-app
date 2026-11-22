"""BE-04: storage usage ledger: upload, replace, delete, recompute, command."""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone

from businesses.services import create_business_profile
from categories.models import Category
from content.models import Post, Reel
from monetization.models import StoredFile
from monetization.storage_usage import get_used_bytes, recompute_used_bytes
from products.models import Product
from stories.models import Story
from stories.tasks import expire_stale_stories

User = get_user_model()
pytestmark = pytest.mark.django_db


def _business(suffix="a"):
    email = f"storage-usage-{suffix}@example.com"
    user = User.objects.create_user(
        username=email, email=email, password="testpass123", account_type="business"
    )
    return create_business_profile(
        user=user,
        business_name=f"Storage Trader {suffix}",
        business_type="trader",
        country="EG",
        city="Ismailia",
    )


def _file(name, size):
    return SimpleUploadedFile(name, b"x" * size)


def _product(business, size=1000):
    category = Category.objects.get_or_create(name="Fashion")[0]
    return Product.objects.create(
        business=business,
        category=category,
        name="P",
        price="10.00",
        currency="EGP",
        image=_file("p.png", size),
    )


def _post(business, size=1000):
    return Post.objects.create(
        business=business, caption="c", image=_file("post.png", size)
    )


def _reel(business, video=5000, thumb=300):
    return Reel.objects.create(
        business=business,
        caption="c",
        video=_file("r.mp4", video),
        thumbnail=_file("t.jpg", thumb),
    )


def _story(business, size=700):
    return Story.objects.create(business=business, media=_file("s.jpg", size))


class TestUploadIncreasesUsage:
    def test_no_content_is_zero(self):
        assert get_used_bytes(_business()) == 0

    def test_product_post_reel_story_all_count(self):
        business = _business()
        _product(business, 1000)
        _post(business, 2000)
        _reel(business, 5000, 300)
        _story(business, 700)

        assert get_used_bytes(business) == 1000 + 2000 + 5000 + 300 + 700

    def test_reel_without_thumbnail_counts_video_only(self):
        business = _business()
        Reel.objects.create(business=business, caption="c", video=_file("r.mp4", 4000))

        assert get_used_bytes(business) == 4000

    def test_other_business_is_not_affected(self):
        a, b = _business("a"), _business("b")
        _post(a, 1500)

        assert get_used_bytes(a) == 1500
        assert get_used_bytes(b) == 0

    def test_editing_without_changing_files_does_not_change_usage(self):
        business = _business()
        post = _post(business, 1000)
        post.caption = "edited"
        post.save()

        assert get_used_bytes(business) == 1000
        assert StoredFile.objects.filter(business=business).count() == 1

    def test_avatar_is_not_counted(self):
        business = _business()
        business.avatar.save("a.png", ContentFile(b"x" * 900))

        assert get_used_bytes(business) == 0
        assert recompute_used_bytes(business) == 0


class TestReplacement:
    def test_replacing_a_file_adjusts_by_the_difference_and_deletes_the_old_one(
        self, django_capture_on_commit_callbacks
    ):
        business = _business()
        post = _post(business, 1000)
        old_name = post.image.name

        post.image = _file("new.png", 400)
        with django_capture_on_commit_callbacks(execute=True):
            post.save()

        assert get_used_bytes(business) == 400
        assert not default_storage.exists(old_name)
        assert default_storage.exists(post.image.name)

    def test_reel_transcode_style_replacement_is_tracked(
        self, django_capture_on_commit_callbacks
    ):
        business = _business()
        reel = _reel(business, 5000, 300)
        raw = reel.video.name

        reel.video.save("1_transcoded.mp4", ContentFile(b"y" * 1200), save=False)
        reel.thumbnail.save("1_thumb.jpg", ContentFile(b"y" * 100), save=False)
        with django_capture_on_commit_callbacks(execute=True):
            reel.save()

        assert get_used_bytes(business) == 1200 + 100
        assert not default_storage.exists(raw)

    def test_partial_save_of_another_field_is_ignored(self):
        business = _business()
        reel = _reel(business)
        reel.processing_status = Reel.ProcessingStatus.PROCESSING
        reel.save(update_fields=["processing_status"])

        assert get_used_bytes(business) == 5300


class TestDeletion:
    def test_hard_delete_decreases_usage_and_removes_the_file(
        self, django_capture_on_commit_callbacks
    ):
        business = _business()
        product = _product(business, 1000)
        name = product.image.name

        with django_capture_on_commit_callbacks(execute=True):
            product.hard_delete()

        assert get_used_bytes(business) == 0
        assert not default_storage.exists(name)

    def test_soft_delete_purges_the_file_and_decreases_usage(
        self, django_capture_on_commit_callbacks
    ):
        business = _business()
        post = _post(business, 2000)
        name = post.image.name

        with django_capture_on_commit_callbacks(execute=True):
            post.delete()

        assert get_used_bytes(business) == 0
        assert not default_storage.exists(name)
        assert not Post.all_objects.get(pk=post.pk).image

    def test_soft_deleting_a_reel_removes_video_and_thumbnail(
        self, django_capture_on_commit_callbacks
    ):
        business = _business()
        reel = _reel(business)
        names = [reel.video.name, reel.thumbnail.name]

        with django_capture_on_commit_callbacks(execute=True):
            reel.delete()

        assert get_used_bytes(business) == 0
        assert not any(default_storage.exists(n) for n in names)

    def test_bulk_queryset_delete_decreases_usage(
        self, django_capture_on_commit_callbacks
    ):
        business = _business()
        _post(business, 1000)
        _post(business, 2000)

        with django_capture_on_commit_callbacks(execute=True):
            Post.all_objects.filter(business=business).delete()  # hard, bulk

        assert get_used_bytes(business) == 0

    def test_file_is_kept_until_the_transaction_commits(
        self, django_capture_on_commit_callbacks
    ):
        business = _business()
        post = _post(business, 1000)
        name = post.image.name

        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            post.hard_delete()

        assert get_used_bytes(business) == 0
        assert default_storage.exists(name)  # nothing deleted before commit
        for callback in callbacks:
            callback()
        assert not default_storage.exists(name)

    def test_shared_file_is_not_deleted_while_still_referenced(
        self, django_capture_on_commit_callbacks
    ):
        business = _business()
        first, second = _post(business, 1000), _post(business, 1000)
        shared = first.image.name
        Post.all_objects.filter(pk=second.pk).update(image=shared)
        recompute_used_bytes(business)

        with django_capture_on_commit_callbacks(execute=True):
            first.hard_delete()

        assert default_storage.exists(shared)


class TestStoryExpiry:
    def test_expired_story_media_is_deleted_and_stops_counting(
        self, django_capture_on_commit_callbacks
    ):
        business = _business()
        story = _story(business, 700)
        name = story.media.name
        Story.all_objects.filter(pk=story.pk).update(
            expires_at=timezone.now() - timezone.timedelta(minutes=1)
        )

        with django_capture_on_commit_callbacks(execute=True):
            result = expire_stale_stories()

        story = Story.all_objects.get(pk=story.pk)
        assert result == {"archived_count": 1}
        assert story.archived_at is not None
        assert not story.media
        assert not default_storage.exists(name)
        assert get_used_bytes(business) == 0

    def test_active_story_still_counts(self, django_capture_on_commit_callbacks):
        business = _business()
        _story(business, 700)

        with django_capture_on_commit_callbacks(execute=True):
            expire_stale_stories()

        assert get_used_bytes(business) == 700


class TestRecompute:
    def test_recompute_fixes_a_wrong_number(self):
        business = _business()
        _post(business, 1000)
        _reel(business, 5000, 300)
        StoredFile.objects.filter(business=business).update(size_bytes=1)

        assert recompute_used_bytes(business, refresh_sizes=True) == 6300

    def test_recompute_rebuilds_missing_ledger_rows(self):
        business = _business()
        _post(business, 1000)
        StoredFile.objects.all().delete()
        assert get_used_bytes(business) == 0

        assert recompute_used_bytes(business) == 1000

    def test_recompute_drops_orphan_rows(self):
        business = _business()
        StoredFile.objects.create(
            business=business, model="content.Post", object_id=999999,
            field="image", file_name="gone.png", size_bytes=777,
        )

        assert recompute_used_bytes(business) == 0

    def test_recompute_ignores_soft_deleted_rows_by_default(self):
        business = _business()
        post = _post(business, 1000)
        Post.all_objects.filter(pk=post.pk).update(is_deleted=True)

        assert recompute_used_bytes(business) == 0
        assert default_storage.exists(post.image.name)  # not purged

    def test_purge_deleted_removes_legacy_soft_deleted_files(
        self, django_capture_on_commit_callbacks
    ):
        business = _business()
        post = _post(business, 1000)
        name = post.image.name
        Post.all_objects.filter(pk=post.pk).update(is_deleted=True)

        with django_capture_on_commit_callbacks(execute=True):
            used = recompute_used_bytes(business, purge_deleted=True)

        assert used == 0
        assert not default_storage.exists(name)


class TestCommand:
    def test_command_with_business_option(self):
        a, b = _business("a"), _business("b")
        _post(a, 1000)
        _post(b, 2000)
        StoredFile.objects.all().delete()
        out = StringIO()

        call_command("recompute_storage_usage", business=a.pk, stdout=out)

        assert get_used_bytes(a) == 1000
        assert get_used_bytes(b) == 0  # untouched
        assert f"business {a.pk}: 0 -> 1000" in out.getvalue()

    def test_command_without_option_fixes_everyone(self):
        a, b = _business("a"), _business("b")
        _post(a, 1000)
        _post(b, 2000)
        StoredFile.objects.all().delete()

        call_command("recompute_storage_usage", stdout=StringIO())

        assert get_used_bytes(a) == 1000
        assert get_used_bytes(b) == 2000

    def test_command_unknown_business_errors(self):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("recompute_storage_usage", business=999999)