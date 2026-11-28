from django.core.management.base import BaseCommand

from monetization.purge import process_pending_file_deletions, purge_due_businesses


class Command(BaseCommand):
    help = "Purge content of ended subscriptions past purge_at. Dry run by default."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="Default.")
        mode.add_argument(
            "--execute", action="store_true", help="PERMANENTLY delete."
        )

    def handle(self, *args, **options):
        execute = options["execute"]
        self.stdout.write("EXECUTE" if execute else "DRY RUN (nothing is deleted)")
        if execute:
            self.stdout.write(f"retries: {process_pending_file_deletions()}")
        summary = purge_due_businesses(dry_run=not execute)
        for r in summary.pop("reports"):
            self.stdout.write(
                f"business={r.business_id} files={r.files} bytes={r.bytes} "
                f"counts={r.counts}"
            )
        self.stdout.write(str(summary))