"""
Part P-059 — shared test helpers for the feed app.

Timestamps are set explicitly (created_at is auto_now_add, so it is
overwritten with a queryset update right after creation). `ts(n)` gives
a fixed instant: a larger n is a NEWER item, so tests read naturally.
"""

import itertools
from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model

from businesses.models import BusinessProfile
from businesses.services import create_business_profile
from content.models import Post, Reel

User = get_user_model()

_BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_counter = itertools.count(1)


def ts(minutes: int) -> datetime:
    return _BASE + timedelta(minutes=minutes)


def make_business(name: str, *, featured: bool = False) -> BusinessProfile:
    email = f"feed-{next(_counter)}@example.com"
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
    if featured:
        BusinessProfile.objects.filter(pk=business.pk).update(is_featured=True)
        business.refresh_from_db()
    return business


def make_customer():
    email = f"feed-customer-{next(_counter)}@example.com"
    return User.objects.create_user(
        username=email, email=email, password="testpass123", account_type="customer"
    )


def make_post(business, *, at: datetime, status: str = "published", caption="post"):
    post = Post.objects.create(business=business, caption=caption, status=status)
    Post.objects.filter(pk=post.pk).update(created_at=at)
    post.refresh_from_db()
    return post


def make_reel(
    business,
    *,
    at: datetime,
    status: str = "published",
    processing_status: str = "ready",
    caption="reel",
):
    # `video` is given as a plain storage path: FileField does not upload
    # anything for a string value, so these tests never touch object storage.
    reel = Reel.objects.create(
        business=business,
        caption=caption,
        video="reels/videos/feed-test.mp4",
        status=status,
        processing_status=processing_status,
    )
    Reel.objects.filter(pk=reel.pk).update(created_at=at)
    reel.refresh_from_db()
    return reel