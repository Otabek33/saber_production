import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from finance_saber.audit.models import AuditEvent


class Command(BaseCommand):
    help = (
        "Delete action log entries older than --days (default 365), so the log doesn't grow forever. "
        "Run it from a scheduled job, e.g. once a month."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=365, help="Keep this many days (at least 30)")
        parser.add_argument("--dry-run", action="store_true", help="Only count what would be deleted")

    def handle(self, days, dry_run, **options):
        if days < 30:
            raise CommandError("Keep at least 30 days of the log.")
        old = AuditEvent.objects.filter(created_at__lt=timezone.now() - datetime.timedelta(days=days))
        count = old.count()
        if dry_run:
            self.stdout.write(f"{count} entries are older than {days} days; nothing deleted (dry run).")
            return
        old.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {count} entries older than {days} days."))
