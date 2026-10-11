"""
Rebuilds Product.search_vector / BusinessProfile.search_vector for every
row (soft-deleted ones included).

The post_save signal only keeps vectors fresh for rows saved through the
ORM's .save(); rows created before the signal existed, bulk-created or
changed with QuerySet.update() keep a NULL/stale vector. Search no longer
depends on the vector alone (see search/services.py), but a correct
vector still gives better ranking, so run this once after deploying:

    python manage.py rebuild_search_vectors
"""

from django.contrib.postgres.search import SearchVector
from django.core.management.base import BaseCommand

from businesses.models import BusinessProfile
from products.models import Product


class Command(BaseCommand):
    help = "Rebuild search_vector for all products and business profiles."

    def handle(self, *args, **options):
        products = Product.all_objects.all().update(
            search_vector=SearchVector("name", "description")
        )
        businesses = BusinessProfile.all_objects.all().update(
            search_vector=SearchVector("business_name", "description")
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Rebuilt search_vector: {products} products, {businesses} businesses."
            )
        )
