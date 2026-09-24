from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from rest_framework.test import APIClient

from businesses.services import create_business_profile
from categories.models import Category
from content.models import Post, Reel
from products.models import Product
from social.models import Comment
from stories.models import Story

User = get_user_model()
PASSWORD = "StrongPass123!"


def make_user(name, account_type="customer"):
    return User.objects.create_user(
        username=f"{name}@example.com",
        email=f"{name}@example.com",
        password=PASSWORD,
        account_type=account_type,
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_business(name="owner"):
    owner = make_user(name, account_type="business")
    return create_business_profile(
        user=owner,
        business_name=f"{name} shop",
        business_type="trader",
        country="Egypt",
        city="Cairo",
    )


def make_target(kind, business, published=True):
    """Create a reportable target of ``kind`` owned by ``business``.

    ``published=False`` leaves Post/Reel/Story in their default (pending)
    state so tests can prove unpublished content is not reportable.
    """
    if kind == "business":
        return business
    if kind == "product":
        category, _ = Category.objects.get_or_create(name="Fashion")
        return Product.objects.create(
            business=business,
            category=category,
            name="Test product",
            description="Test description",
            price=Decimal("10.00"),
            currency="EGP",
        )
    if kind == "post":
        extra = {"status": Post.Status.PUBLISHED} if published else {}
        return Post.objects.create(
            business=business,
            caption="Test post",
            image="posts/test.jpg",
            **extra,
        )
    if kind == "reel":
        extra = (
            {"status": Reel.Status.PUBLISHED, "processing_status": "ready"}
            if published
            else {}
        )
        return Reel.objects.create(
            business=business,
            caption="Test reel",
            video="reels/test.mp4",
            **extra,
        )
    if kind == "story":
        extra = {"status": Story.Status.PUBLISHED} if published else {}
        return Story.objects.create(
            business=business,
            media="stories/test.jpg",
            **extra,
        )
    raise ValueError(f"Unknown target kind: {kind}")


def make_comment(post, author, text="A comment"):
    # Comment is a plain generic-FK row; it is created directly (not through
    # the API) so tests control exactly who authored it.
    return Comment.objects.create(
        user=author,
        content_type=ContentType.objects.get_for_model(Post),
        object_id=post.pk,
        text=text,
    )


def submit(client, kind, object_id, reason="spam", **extra):
    payload = {"content_type": kind, "object_id": object_id, "reason": reason}
    payload.update(extra)
    return client.post(reverse("reports:create"), payload, format="json")
