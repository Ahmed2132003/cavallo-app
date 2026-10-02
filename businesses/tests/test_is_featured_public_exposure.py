"""
Part P-110 (STEP 1) -- is_featured exposure on the public serializers.

The Flutter "Featured" badge (display-only, ADR-006) needs the owning
business's real Featured state (P-087) on every payload it already
consumes: public business profile, Search business/product results
(BusinessProfileSerializer / ProductSerializer), public Posts/Reels and
the Home/Discover feed items (PostPublicSerializer / ReelPublicSerializer).

For Post/Reel/Product the flag is resolved through the business join; no
is_featured column is added to them (pinned by P-087's guard test).
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from businesses.models import BusinessProfile
from businesses.serializers import BusinessProfileSerializer
from businesses.services import create_business_profile
from businesses.views import _business_profile_cache_key
from categories.models import Category
from content.models import Post, Reel
from content.serializers import PostPublicSerializer, ReelPublicSerializer
from content.tests.test_public_api import _make_video_upload
from feed.cursor import CONTENT_TYPE_POST, CONTENT_TYPE_REEL
from feed.serializers import FeedItemSerializer
from feed.services import FeedEntry
from products.models import Product
from products.serializers import ProductSerializer

User = get_user_model()


def _make_business(email, name, featured=False):
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


def _make_product(business, name="P110 Product"):
    category = Category.objects.create(name=f"P110 Category {name}")
    return Product.objects.create(
        business=business,
        category=category,
        name=name,
        description="desc",
        price="10.00",
        currency=Product.CURRENCY_EGP,
    )


class TestBusinessProfileSerializerIsFeatured(APITestCase):
    def setUp(self):
        self.plain = _make_business("p110-plain-biz@example.com", "P110 Plain")
        self.featured = _make_business(
            "p110-feat-biz@example.com", "P110 Featured", featured=True
        )

    def test_serializer_reports_false_for_non_featured(self):
        data = BusinessProfileSerializer(self.plain).data
        self.assertIn("is_featured", data)
        self.assertIs(data["is_featured"], False)

    def test_serializer_reports_true_for_featured(self):
        data = BusinessProfileSerializer(self.featured).data
        self.assertIs(data["is_featured"], True)

    def test_public_endpoint_exposes_flag_for_both_states(self):
        for business, expected in ((self.plain, False), (self.featured, True)):
            cache.delete(_business_profile_cache_key(business.pk))
            response = self.client.get(f"/api/v1/businesses/{business.pk}/")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertIs(response.data["is_featured"], expected)

    def test_flag_is_read_only_on_the_serializer(self):
        serializer = BusinessProfileSerializer(self.plain, data={}, partial=True)
        self.assertTrue(serializer.fields["is_featured"].read_only)


class TestPublicPostIsFeatured(APITestCase):
    def setUp(self):
        self.plain = _make_business("p110-post-plain@example.com", "P110 Post Plain")
        self.featured = _make_business(
            "p110-post-feat@example.com", "P110 Post Featured", featured=True
        )
        self.plain_post = Post.objects.create(
            business=self.plain, caption="plain", status="published"
        )
        self.featured_post = Post.objects.create(
            business=self.featured, caption="featured", status="published"
        )

    def _results(self, business):
        response = self.client.get(
            "/api/v1/posts/public/", {"business_id": business.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["results"]

    def test_public_list_flag_false_for_non_featured_business(self):
        results = self._results(self.plain)
        self.assertEqual(len(results), 1)
        self.assertIs(results[0]["is_featured"], False)

    def test_public_list_flag_true_for_featured_business(self):
        results = self._results(self.featured)
        self.assertEqual(len(results), 1)
        self.assertIs(results[0]["is_featured"], True)

    def test_flag_follows_business_state_not_the_post(self):
        self.assertIs(PostPublicSerializer(self.plain_post).data["is_featured"], False)
        BusinessProfile.objects.filter(pk=self.plain.pk).update(is_featured=True)
        fresh = Post.objects.get(pk=self.plain_post.pk)
        self.assertIs(PostPublicSerializer(fresh).data["is_featured"], True)

    def test_public_list_query_count_does_not_grow_with_rows(self):
        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(
                    "/api/v1/posts/public/", {"business_id": self.featured.pk}
                )
            return len(ctx)

        baseline = count_queries()
        for i in range(3):
            Post.objects.create(
                business=self.featured, caption=f"extra {i}", status="published"
            )
        self.assertEqual(count_queries(), baseline)


class TestPublicReelIsFeatured(APITestCase):
    def setUp(self):
        self.plain = _make_business("p110-reel-plain@example.com", "P110 Reel Plain")
        self.featured = _make_business(
            "p110-reel-feat@example.com", "P110 Reel Featured", featured=True
        )
        self.plain_reel = self._reel(self.plain, "plain")
        self.featured_reel = self._reel(self.featured, "featured")

    def _reel(self, business, caption):
        return Reel.objects.create(
            business=business,
            caption=caption,
            video=_make_video_upload(f"{caption}.mp4"),
            status="published",
            processing_status="ready",
        )

    def _results(self, business):
        response = self.client.get(
            "/api/v1/reels/public/", {"business_id": business.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["results"]

    def test_public_list_flag_false_for_non_featured_business(self):
        results = self._results(self.plain)
        self.assertEqual(len(results), 1)
        self.assertIs(results[0]["is_featured"], False)

    def test_public_list_flag_true_for_featured_business(self):
        results = self._results(self.featured)
        self.assertEqual(len(results), 1)
        self.assertIs(results[0]["is_featured"], True)

    def test_serializer_flag_follows_business_state(self):
        self.assertIs(
            ReelPublicSerializer(self.plain_reel).data["is_featured"], False
        )
        BusinessProfile.objects.filter(pk=self.plain.pk).update(is_featured=True)
        fresh = Reel.objects.get(pk=self.plain_reel.pk)
        self.assertIs(ReelPublicSerializer(fresh).data["is_featured"], True)


class TestProductIsFeatured(APITestCase):
    def setUp(self):
        self.plain = _make_business("p110-prod-plain@example.com", "P110 Prod Plain")
        self.featured = _make_business(
            "p110-prod-feat@example.com", "P110 Prod Featured", featured=True
        )
        self.plain_product = _make_product(self.plain, "Plain Product")
        self.featured_product = _make_product(self.featured, "Featured Product")

    def test_serializer_flag_false_for_non_featured_business(self):
        data = ProductSerializer(self.plain_product).data
        self.assertIs(data["is_featured"], False)

    def test_serializer_flag_true_for_featured_business(self):
        data = ProductSerializer(self.featured_product).data
        self.assertIs(data["is_featured"], True)

    def test_public_list_exposes_flag_for_both_states(self):
        url = reverse("products:product-public-list")
        for business, expected in ((self.plain, False), (self.featured, True)):
            response = self.client.get(url, {"business_id": business.pk})
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(len(response.data["results"]), 1)
            self.assertIs(response.data["results"][0]["is_featured"], expected)

    def test_model_has_no_duplicated_is_featured_column(self):
        field_names = {f.name for f in Product._meta.get_fields()}
        self.assertNotIn("is_featured", field_names)


class TestFeedItemIsFeatured(APITestCase):
    def setUp(self):
        self.plain = _make_business("p110-feed-plain@example.com", "P110 Feed Plain")
        self.featured = _make_business(
            "p110-feed-feat@example.com", "P110 Feed Featured", featured=True
        )

    def test_post_feed_item_carries_flag(self):
        for business, expected in ((self.plain, False), (self.featured, True)):
            post = Post.objects.create(
                business=business, caption="feed", status="published"
            )
            entry = FeedEntry(content_type=CONTENT_TYPE_POST, obj=post)
            data = FeedItemSerializer(entry).data
            self.assertEqual(data["content_type"], "post")
            self.assertIs(data["is_featured"], expected)

    def test_reel_feed_item_carries_flag(self):
        for business, expected in ((self.plain, False), (self.featured, True)):
            reel = Reel.objects.create(
                business=business,
                caption="feed",
                video=_make_video_upload("feed.mp4"),
                status="published",
                processing_status="ready",
            )
            entry = FeedEntry(content_type=CONTENT_TYPE_REEL, obj=reel)
            data = FeedItemSerializer(entry).data
            self.assertEqual(data["content_type"], "reel")
            self.assertIs(data["is_featured"], expected)
