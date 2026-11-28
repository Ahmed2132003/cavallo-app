"""BE-09 - permanent content purge."""

from datetime import timedelta
from io import StringIO
from unittest import mock

import pytest
from django.conf import settings as django_settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import models
from django.utils import timezone

from businesses.models import BusinessProfile
from categories.models import Category
from content.models import Post, Reel
from core.storage_backends import MediaStorage
from moderation.models import ModerationQueue
from monetization import purge
from monetization.models import PendingFileDeletion, StoredFile, Subscription
from monetization.services import create_free_subscription
from monetization.storage_usage import get_used_bytes
from monetization.tasks import purge_expired_free_trials
from monetization.tests.test_lifecycle import _buy, _checkout, _sub
from payments.models import Subscription as Payment
from payments.tests.factories import make_business, make_storage_plan
from products.models import Product, ProductVariant
from social.models import Comment, Follow, Like, Save, Share
from stories.models import Story, StoryView

pytestmark = pytest.mark.django_db

CONTENT = (Product, Post, Reel, Story)
INTERACTIONS = (Like, Comment, Save, Share)


@pytest.fixture(autouse=True)
def memory_storage(settings):
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }


def _category():
    values = {}
    for f in Category._meta.fields:
        if f.primary_key or f.null or f.blank or f.has_default():
            continue
        if isinstance(f, models.CharField):
            values[f.name] = f"be09{timezone.now().timestamp()}"[-f.max_length :]
    return Category._base_manager.create(**values)


def _file(name):
    return SimpleUploadedFile(name, b"12345")


def _content(business):
    product = Product._base_manager.create(
        business=business,
        category=_category(),
        name="p",
        price=1,
        currency="EGP",
        image=_file("p.jpg"),
    )
    ProductVariant.objects.create(product=product, name="size", value="L")
    post = Post._base_manager.create(
        business=business, caption="c", image=_file("post.jpg")
    )
    reel = Reel._base_manager.create(
        business=business,
        caption="c",
        video=_file("v.mp4"),
        thumbnail=_file("t.jpg"),
    )
    # Soft-deleted rows are purged too. .update() skips the BE-04 signals,
    # like a legacy row whose files were never removed.
    Reel._base_manager.filter(pk=reel.pk).update(is_deleted=True)
    story = Story._base_manager.create(business=business, media=_file("s.jpg"))
    StoryView.objects.create(story=story, viewer=business.user)
    for target in (post, reel):
        Like.objects.create(user=business.user, content_object=target)
        Comment.objects.create(user=business.user, content_object=target, text="x")
        Share.objects.create(user=business.user, content_object=target)
    Save.objects.create(user=business.user, content_object=product)
    return list(
        StoredFile.objects.filter(business=business).values_list(
            "file_name", flat=True
        )
    )


def _ended(business, plan, paid=False, **overrides):
    now = timezone.now()
    if paid:
        values = {
            "status": "expired",
            "current_period_end": now - timedelta(days=61),
        }
    else:
        values = {
            "status": "locked",
            "trial_ends_at": now - timedelta(days=3),
            "current_period_start": None,
            "current_period_end": None,
        }
    values["purge_at"] = now - timedelta(hours=1)
    values.update(overrides)
    return _sub(business, plan, **values)


def _count(business, model):
    return model._base_manager.filter(business=business).count()


def _setup(paid=False, **overrides):
    plan = make_storage_plan("pro", storage_gb=10)
    business = make_business("owner")
    names = _content(business)
    sub = _ended(business, plan, paid, **overrides)
    return plan, business, sub, names


def _assert_intact(business, names):
    assert all(_count(business, model) == 1 for model in CONTENT)
    assert all(default_storage.exists(name) for name in names)


def test_dry_run_deletes_nothing():
    _, business, sub, names = _setup()

    report = purge.purge_business_content(business, dry_run=True)

    assert not report.purged
    assert report.files == 5
    assert report.bytes == 25
    _assert_intact(business, names)
    assert PendingFileDeletion.objects.count() == 0
    sub.refresh_from_db()
    assert sub.purged_at is None


def test_command_defaults_to_dry_run():
    _, business, _, names = _setup()
    out = StringIO()

    call_command("purge_free_trials", stdout=out)

    assert "DRY RUN" in out.getvalue()
    _assert_intact(business, names)


@pytest.mark.parametrize("paid", [False, True])
def test_execute_deletes_content_and_files_only(
    paid, settings, django_capture_on_commit_callbacks
):
    settings.PAYMENT_GATEWAY = "payments.tests.fake_gateway.FakeGateway"
    plan, business, sub, names = _setup(paid)
    assert _checkout(business, {"plan": "pro"}).status_code == 201
    other = make_business("other", phone_number="+201001234569")
    other_names = _content(other)
    Follow.objects.create(follower=other.user, business=business)
    likes_before = Like.objects.count()
    assert ModerationQueue.objects.count() > 0
    queue_before = ModerationQueue.objects.count()

    with django_capture_on_commit_callbacks(execute=True):
        call_command("purge_free_trials", "--execute", stdout=StringIO())

    assert all(_count(business, model) == 0 for model in CONTENT)
    assert not any(default_storage.exists(name) for name in names)
    assert get_used_bytes(business) == 0
    assert PendingFileDeletion.objects.count() == 0
    assert Like.objects.count() == likes_before // 2
    assert all(model._base_manager.count() > 0 for model in INTERACTIONS)
    assert ProductVariant.objects.count() == 1
    assert StoryView.objects.count() == 1
    assert ModerationQueue.objects.count() == queue_before // 2
    _assert_intact(other, other_names)
    assert BusinessProfile.all_objects.filter(pk=business.pk).exists()
    assert type(business.user).objects.filter(pk=business.user.pk).exists()
    assert Follow.objects.count() == 1
    assert Payment.objects.count() == 1
    sub.refresh_from_db()
    assert sub.purged_at is not None


def test_idempotent_and_no_new_trial_then_starts_empty(
    django_capture_on_commit_callbacks,
):
    plan, business, sub, _ = _setup()
    with django_capture_on_commit_callbacks(execute=True):
        assert purge.purge_business_content(business).purged

    assert purge.purge_business_content(business).skipped_reason == "already_purged"
    business.save()
    assert create_free_subscription(business).pk == sub.pk
    assert Subscription.objects.get(business=business).status == "locked"

    renewed = _buy(business, plan)
    assert renewed.status == "active"
    assert get_used_bytes(business) == 0


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"status": "active"}, "not_ended"),
        ({"status": "trialing"}, "not_ended"),
        ({"purge_at": None}, "missing_dates"),
        ({"purge_at": timezone.now() + timedelta(days=1)}, "not_due"),
        # stale purge_at, but the paid grace has not passed
        ({"current_period_end": timezone.now() - timedelta(days=10)}, "not_due"),
        ({"purged_at": timezone.now()}, "already_purged"),
    ],
)
def test_guard_blocks(overrides, reason):
    _, business, _, names = _setup(paid=True, **overrides)

    report = purge.purge_business_content(business)

    assert report.skipped_reason == reason
    _assert_intact(business, names)


def test_purge_before_last_end_does_not_block_a_new_expiry():
    _, _, sub, _ = _setup(
        paid=True, purged_at=timezone.now() - timedelta(days=200)
    )

    assert purge.purge_blocker(sub, timezone.now()) is None


@pytest.mark.parametrize("paid", [False, True])
def test_renewal_before_purge_date_is_skipped(paid, settings):
    settings.STORAGE_PURGE_ENABLED = True
    plan, business, _, names = _setup(
        paid, purge_at=timezone.now() + timedelta(days=1)
    )
    _buy(business, plan)

    later = timezone.now() + timedelta(days=2)
    assert purge.purge_due_businesses(dry_run=False, now=later)["purged"] == 0
    assert purge_expired_free_trials()["purged"] == 0
    _assert_intact(business, names)


def test_task_is_disabled_by_default(settings):
    settings.STORAGE_PURGE_ENABLED = False
    _, business, _, names = _setup()

    result = purge_expired_free_trials()

    assert result["purged"] == 0
    assert result["eligible"] == 1
    _assert_intact(business, names)


def test_storage_failure_is_kept_and_retried(django_capture_on_commit_callbacks):
    _, business, _, names = _setup()
    target = "django.core.files.storage.InMemoryStorage.delete"

    with mock.patch(target, side_effect=OSError("s3 down")):
        with django_capture_on_commit_callbacks(execute=True):
            purge.purge_business_content(business)

    assert PendingFileDeletion.objects.count() == len(names)
    assert all(row.attempts == 1 for row in PendingFileDeletion.objects.all())

    assert purge.process_pending_file_deletions()["deleted"] == len(names)
    assert PendingFileDeletion.objects.count() == 0
    assert not any(default_storage.exists(name) for name in names)


def test_unexpected_cascade_rolls_everything_back():
    _, business, sub, names = _setup()
    allowed = purge.ALLOWED_DELETES - {"products.productvariant"}

    with mock.patch.object(purge, "ALLOWED_DELETES", allowed):
        with pytest.raises(purge.UnsafeCascade):
            purge.purge_business_content(business)

    _assert_intact(business, names)
    assert Like.objects.count() == 2
    sub.refresh_from_db()
    assert sub.purged_at is None


def test_one_failure_does_not_stop_the_others():
    plan, first, _, _ = _setup()
    second = make_business("other", phone_number="+201001234569")
    _ended(second, plan)
    real = purge.purge_business_content

    def flaky(business_id, **kwargs):
        if business_id == first.pk:
            raise RuntimeError("boom")
        return real(business_id, **kwargs)

    with mock.patch.object(purge, "purge_business_content", side_effect=flaky):
        summary = purge.purge_due_businesses(dry_run=False)

    assert summary["failed"] == 1
    assert summary["purged"] == 1


def test_s3_backend_delete_reaches_the_bucket():
    with mock.patch.object(
        MediaStorage, "bucket", new_callable=mock.PropertyMock
    ) as bucket:
        MediaStorage().delete("posts/a.jpg")

    bucket.return_value.Object.assert_called_once_with("posts/a.jpg")
    bucket.return_value.Object.return_value.delete.assert_called_once()


def test_beat_registration():
    entry = django_settings.CELERY_BEAT_SCHEDULE["purge-expired-free-trials"]
    assert entry["task"] == "monetization.purge_expired_free_trials"