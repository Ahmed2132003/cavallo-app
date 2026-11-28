"""
BE-04: repair storage-usage drift.

    python manage.py recompute_storage_usage [--business ID]
                                             [--refresh-sizes] [--purge-deleted]

Run once after deploying BE-04: it fills the ledger for all existing files
(one storage size call per file).
"""

from django.core.management.base import BaseCommand, CommandError

from businesses.models import BusinessProfile
from monetization.storage_usage import get_used_bytes, recompute_used_bytes


class Command(BaseCommand):
    help = "Rebuild per-business storage usage from the real files."

    def add_arguments(self, parser):
        parser.add_argument("--business", type=int, help="BusinessProfile id.")
        parser.add_argument(
            "--refresh-sizes",
            action="store_true",
            help="Re-read every file size from storage.",
        )
        parser.add_argument(
            "--purge-deleted",
            action="store_true",
            help="Also delete the files of content soft-deleted before BE-04.",
        )

    def handle(self, *args, **options):
        businesses = BusinessProfile.all_objects.order_by("pk")
        if options["business"] is not None:
            businesses = businesses.filter(pk=options["business"])
            if not businesses.exists():
                raise CommandError(f"Business {options['business']} not found.")

        checked = changed = 0
        for business in businesses.iterator():
            before = get_used_bytes(business)
            after = recompute_used_bytes(
                business,
                refresh_sizes=options["refresh_sizes"],
                purge_deleted=options["purge_deleted"],
            )
            checked += 1
            if before != after:
                changed += 1
                self.stdout.write(f"business {business.pk}: {before} -> {after} bytes")
        self.stdout.write(f"Checked {checked} business(es), corrected {changed}.")